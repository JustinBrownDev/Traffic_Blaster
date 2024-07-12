# Justin Brown - 7/8/2024 - Traffic Blaster
import grequests  # async reqs
#Basic Imports
import os  # list dir, path exists, etc.
import random  # for testing
from datetime import datetime, timedelta  # for action delays
# noinspection PyUnresolvedReferences
import traceback  # for debugging
import requests

import subprocess  # subprocess to run openvpn instance

from threading import Thread  # threads for non-blocking read from openvpn process's output
from queue import Queue, Empty  # queue for the same as above


# getting the output buffers without hanging everything was challenging

# threaded function
def ADD_OVPN_OUTPUT_TO_QUEUE(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)


# User Defined functions -- Default versions
def DEFAULT_GET_OVPN_COMMAND_STRING(session):
    return f"""\"{session.ovpn_exe_path}\" \
--config \"{session.ovpn_config_path}\" \
--disable-dco \
--connect-retry-max {session.ovpn_connect_retry_max} \
--connect-timeout {session.ovpn_connect_timeout} \
--server-poll-timeout {session.ovpn_poll_timeout} \
--resolv-retry {session.ovpn_resolve_retry} \
--auth-user-pass {session.ovpn_credentials_path} \
--verb 4"""

def DEFAULT_CREATE_SESSION():
    return requests.session()
# enums
class TRAFFIC_BLASTER_ENUM:
    ENUM_NAME_DICT = None

    def name(self, num):
        if not self.ENUM_NAME_DICT:  # if its the first time calling name, create the dictionary
            # { VALUE : KEY for KEY in CLASS_VARS if index 0 of KEY is not _ }
            self.ENUM_NAME_DICT = {vars(self.__class__)[i]: i for i in vars(self.__class__) if i[0] != "_"}
        return self.ENUM_NAME_DICT[num]


class TRAFFIC_BLASTER_SESSION_STATE(TRAFFIC_BLASTER_ENUM):
    CLASS_INITIALIZED = 0
    VPN_CONNECTING = 1
    VPN_CONNECTED = 2
    MOUNTING_SESSION = 3
    SESSION_MOUNTED = 4
    TESTING_CONNECTION = 5
    CONNECTION_TESTED = 6
    ONLINE = 7
    CONNECTION_DOWN = -1
    TRAFFIC_MISMATCH = -2
    CONNECTION_ERROR = -3
    TIMED_OUT = -4
    FAILED_TO_MOUNT = -5
    FAILED_TO_GET_VPN_ADDRESS = -6
    CONNECTION_DOWN_TIMEOUT = -7


# ==========================================================
class TB_SESSION:
    def __init__(self, *args):
        self.log = Logger()
        (self.name,
         self.ovpn_exe_path,  # ...
         self.ovpn_config_path,  # ...
         self.ovpn_credentials_path,  # ...
         self.ovpn_connect_retry_max,  # ???????????
         self.ovpn_connect_timeout,  # timeout for initializing openvpn proc
         self.ovpn_poll_timeout,  # ???????
         self.ovpn_verbosity,  # ...
         self.ovpn_resolve_retry,  # ???????
         self.timeout,  # Timeout for class to go online
         self.request_timeout,  # Timeout for user side .get requests
         self.internal_request_timeout,  # Timeout for internally used requests (ip & geo)
         self.get_command_string,
         self.USER_DEFINED_SEND,
         self.USER_DEFINED_RECEIVE,
         self.SESSION_CREATE_FUNCTION
         ) = args
        self.status_lists = []
        self.time_of_last_connection_check = None
        self.ovpn_client_process = None
        self.requests_session = None
        self.time_initialized = None
        self.local_ip = None
        self.remote_ip = None
        self.geolocation = None
        self.update_state(session_state.CLASS_INITIALIZED)
        self.stdout = None
        self.q = None
        self.last_state = self.state
        self.request_list = []
        self.response_list = []

        # Delays
        SESSION_INITIALIZATION_DELAYS = {
            session_state.CLASS_INITIALIZED: 0,
            session_state.VPN_CONNECTED: 3,
            session_state.SESSION_MOUNTED: 10,
            session_state.CONNECTION_TESTED: 10,
            session_state.ONLINE: 5
        }
        self.initialization_step_timer = delay_timer(delay_dictionary=SESSION_INITIALIZATION_DELAYS)
        self.initialization_timeout = delay_timer(default_delay_seconds=self.timeout)
        self.check_connection_api_delay = delay_timer(default_delay_seconds=30)
        self.down_delay = delay_timer(default_delay_seconds=60)

    # User Code
    def open(self):  #open a process
        if self.ovpn_client_process is not None:  # just in case
            print(f"[{self.name}] ovpn client is already open")
            return None

        # Spawn the OpenVPN subprocess
        command_string = self.get_command_string(self)
        self.ovpn_client_process = subprocess.Popen(
            command_string,
            stdout=subprocess.PIPE
        )

        # Thread to continuously read from the subproccess and put outputs on the queue
        self.q = Queue()
        self.stdout = self.ovpn_client_process.stdout

        # Should keep track of this guy !!
        t = Thread(target=ADD_OVPN_OUTPUT_TO_QUEUE, args=(self.stdout, self.q))
        t.daemon = True
        t.start()

        # all done, update state, set initialization timeout to start counting down
        self.update_state(session_state.VPN_CONNECTING)
        self.initialization_timeout.delay()

    def check_initialization_process(self):  # main function, called every loop
        if self.state == session_state.CLASS_INITIALIZED:
            # 1) open() - create OpenVPN process & output queue
            self.open()
            self.update_state(session_state.VPN_CONNECTING)
        elif self.initialization_timeout.ready() and not (
                self.state == session_state.ONLINE or self.state == session_state.CONNECTION_DOWN):
            self.update_state(session_state.TIMED_OUT)
        elif self.initialization_step_timer.ready():  # different steps have different delays
            if self.state == session_state.VPN_CONNECTING:  # 2) read the pipe until "Initialization sequence completed"
                self.read_pipe()
            elif self.state == session_state.VPN_CONNECTED:  # 3) finished, increment state, set delay
                self.initialization_step_timer.delay(self.state)
                self.update_state(session_state.MOUNTING_SESSION)
            elif self.state == session_state.MOUNTING_SESSION:  # 4) try to mount the new connection with a session
                self.mount_session()
            elif self.state == session_state.SESSION_MOUNTED:  # 5) finished, increment state, set delay
                self.initialization_step_timer.delay(self.state)
                self.update_state(session_state.TESTING_CONNECTION)
            elif self.state == session_state.TESTING_CONNECTION:  # 6) check new session's connection with apis
                self.check_connection()
            elif self.state == session_state.CONNECTION_TESTED:  # 7) passed, delay, increment state, clear the timeouts
                self.initialization_step_timer.delay(self.state)
                self.update_state(session_state.ONLINE)
                self.initialization_timeout.clear()
                self.down_delay.clear()
            elif self.state == session_state.ONLINE:  # 8) online, clear timeouts, check connection
                self.check_connection()
                self.initialization_timeout.clear()
                self.down_delay.clear()
            elif self.state == session_state.CONNECTION_DOWN:  # if connection goes offline, start a timeout, keep checking connection
                if self.down_delay.ready():
                    self.update_state(session_state.CONNECTION_DOWN_TIMEOUT)
                else:
                    self.check_connection()
        return self.state

    def get_new_status_dics(self):  # called by main class to get progress info
        status_list = [s for s in self.status_lists]
        self.status_lists.clear()
        return status_list

    def send(self):
        if self.USER_DEFINED_SEND is None:
            print("self.USER_DEFINED_SEND is None")
            return None
        else:
            return self.USER_DEFINED_SEND(self)

    def recieve(self):
        if self.USER_DEFINED_RECEIVE is None:
            print("self.USER_DEFINED_RECIEVE is None")
            return None
        else:
            return self.USER_DEFINED_RECEIVE(self)

    # helper
    def update_state(self, state):  # If state changes, add new status
        self.state = state
        self.append_to_status_list()

    def append_to_status_list(self):  # append status, list of strings to be beautified by the pp class
        self.status_lists.append({"name": self.name, "state": session_state.name(self.state), "remote": self.remote_ip,
                                  "local": self.local_ip, "geolocation": self.geolocation})

    # Check Session
    def check_connection(self):  # get apis to verify IP and Geolocation
        if self.state == session_state.TESTING_CONNECTION:  # if on initialization
            ip = self.request_ip_api()
            if not ip:  # assume no response is a connection error on
                self.update_state(session_state.CONNECTION_ERROR)
                return
            geolocation = self.request_geo_api(ip)
            if not geolocation:
                self.update_state(session_state.CONNECTION_ERROR)
                return
            self.remote_ip = ip
            self.geolocation = geolocation
            self.update_state(session_state.CONNECTION_TESTED)
            self.check_connection_api_delay.delay()

        elif self.check_connection_api_delay.ready():  # if routine connection check, and delay is ready
            self.check_connection_api_delay.delay()  # reset delay
            ip = self.request_ip_api()  #
            if not ip:  # no response
                if self.state == session_state.ONLINE:  # if we were online, now we're not
                    self.down_delay.delay()
                    self.update_state(session_state.CONNECTION_DOWN)
            elif not self.remote_ip == ip:  # wrong ip received, traffic going through wrong endpoint
                self.update_state(session_state.TRAFFIC_MISMATCH)
            else:  # all good
                self.update_state(session_state.ONLINE)
                self.down_delay.clear()

    def request_ip_api(self):
        try:
            sesresponse_ip = self.requests_session.get('http://emapp.cc/get_my_ip',
                                                       timeout=self.internal_request_timeout)
        except TimeoutError:
            return None
        except Exception:
            self.log.write("check_connection",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")

            return None
        if sesresponse_ip.status_code != 200:  # This never happens, probably unnecessary
            print(f"ip api: {sesresponse_ip.status_code}")
        ip = sesresponse_ip.text
        ses_dic_ip = eval(ip)  # this is really stupid, it's literally arbitrary code execution lmao !!
        if type(ses_dic_ip) == dict and "result_data" in ses_dic_ip:  # if response is good
            ip = ses_dic_ip["result_data"]
            return ip
        else:  # if response is bad
            self.log.write("check_connection",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{ip}\n{type(ses_dic_ip)} {ses_dic_ip}")
            #print(f"failed to connect to session IP api : {self.remote_ip}")
            return None

    def request_geo_api(self):
        try:
            sesresponse_geo = self.requests_session.get(f"http://ip-api.com/json/{self.remote_ip}",
                                                        timeout=self.internal_request_timeout)
        except TimeoutError:
            return None
        except Exception:
            self.log.write("check_connection",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
            print(f"failed to connect to session geolocation api {self.geolocation}")
            return None
        if sesresponse_geo.status_code != 200:
            success = False
            print(f"geo api: {sesresponse_geo.status_code}")
        item_geo = sesresponse_geo.text
        if item_geo:  # stupid !!
            ses_dic_geo = eval(item_geo)  # !! dumb!
            item_geo = ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
            return item_geo
    # Networking
    def mount_session(self):
        self.requests_session = self.SESSION_CREATE_FUNCTION() # Default is just requests.Session()
        #try: put a real except statement in if this ever throws
        self.requests_session.get_adapter('http://').init_poolmanager(
            # those are default values from HTTPAdapter's constructor
            connections=requests.adapters.DEFAULT_POOLSIZE,
            maxsize=requests.adapters.DEFAULT_POOLSIZE,
            # This should be a tuple of (address, port). Port 0 means auto-selection.
            source_address=(self.local_ip, 0),
        )
        self.requests_session.get_adapter('https://').init_poolmanager(
            # those are default values from HTTPAdapter's constructor
            connections=requests.adapters.DEFAULT_POOLSIZE,
            maxsize=requests.adapters.DEFAULT_POOLSIZE,
            # This should be a tuple of (address, port). Port 0 means auto-selection.
            source_address=(self.local_ip, 0),
        )
        self.update_state(session_state.SESSION_MOUNTED)
        return True

    # Process Management
    def get_line_from_queue(self):  # return a line if theres a line or None if there is no line
        line = None
        try:
            line = self.q.get_nowait().decode()  # or q.get(timeout=.1)
        except Empty:
            pass
        return line

    def read_pipe(self):
        line = self.get_line_from_queue()

        while line is not None:
            # write line to log, last char is a newline so omit that
            self.log.write(f"ovpn/{self.name}", line[:-1])

            # the timestamp is not always the same length, hacky solution to accommodate that
            line_without_datestr = line[line.find(' ', line.find(' ', line.find(' ') + 1) + 1) + 1:]  # do this better!!
            # look for "Initialization Sequence Completed" and not "Initialization Sequence Completed with Errors"
            # maybe line_without_datestr[:34] == "Initialization Sequence Completed\n" ?
            # meh maybe later !!
            if line_without_datestr[:33] == "Initialization Sequence Completed" and len(line_without_datestr) < 34:
                if self.local_ip is None:
                    self.update_state(session_state.FAILED_TO_GET_VPN_ADDRESS)
                else:
                    self.update_state(session_state.VPN_CONNECTED)
            # this is where we get the ip
            elif line_without_datestr[:55] == 'Notified TAP-Windows driver to set a DHCP IP/netmask of':
                i = 56
                for c in line_without_datestr[i:]:  # char in chars that come after the aforementioned string
                    if c.isnumeric() or c == '.':  # if the char is a number or a period
                        i += 1
                    else:  # if it's not we've hit the end of the ip
                        break
                # slice out the ip
                self.local_ip = line_without_datestr[56:i]

            # get the next line in output
            line = self.get_line_from_queue()

    def terminate(self):
        if self.requests_session:  # we may have not gotten that far
            self.requests_session.close()  # close the session

        self.ovpn_client_process.terminate()  # terminate the process


# ==========================================================
class TrafficBlaster:
    def __init__(self, **args):

        # Class Declarations ==========
        self.log = Logger()  # <--- Replace me with something better please

        self.delay = delay_timer()
        # OPENVPN SETTINGS ============
        self.OPENVPN_EXE_FULL_PATH = args.get("OPENVPN_EXE_FULL_PATH")
        self.OPENVPN_CONFIG_FOLDER_PATH = args.get("OPENVPN_CONFIG_FOLDER_PATH")

        if not (self.OPENVPN_EXE_FULL_PATH and self.OPENVPN_CONFIG_FOLDER_PATH):
            print(
                "you must supply at least the path to an openvpn.exe file, and a path to a directory with .ovpn configuration files")
            print("ex:\n\tTrafficBlaster(OPENVPN_EXE_FULL_PATH = ..., OPENVPN_CONFIG_FOLDER_PATH = ...)")
        self.OPENVPN_CREDENTIALS_PATH = args.get("OPENVPN_CREDENTIALS_PATH")
        self.OVPN_CONNECT_RETRY_MAX = args.get("OVPN_CONNECT_RETRY_MAX",
                                               3)  # this is probably faster at 0, but causes more issues spinning up and destroying connections
        self.OVPN_CONNECT_TIMEOUT = args.get("OVPN_CONNECT_TIMEOUT",
                                             60)  # probably crank this down to like 10 and see what happens, it does not respect me
        self.OVPN_POLL_TIMEOUT = args.get("OVPN_POLL_TIMEOUT", 60)  # not sure exactly, 30 was too low though, maybe
        self.CREATE_VPN_TIMEOUT_SECONDS = self.OVPN_CONNECT_TIMEOUT + self.OVPN_POLL_TIMEOUT + 5  #
        self.OVPN_RESOLVE_RETRY = args.get("OVPN_RESOLVE_RETRY",
                                           0)  # another futile attempt to keep the process from completely hanging
        self.OVPN_PING_RESTART = args.get("OVPN_PING_RESTART", 60)  # keepalive restart
        self.OVPN_PING = args.get("OVPN_PING", 10)  # keepalive
        self.REQUEST_TIMEOUT = 30
        self.INTERNAL_REQUEST_TIMEOUT = 30
        self.SESSION_CLASS_INITIALIZATION_TIMEOUT = args.get("SESSION_CLASS_INITIALIZATION_TIMEOUT", 40)
        # Traffic Blaster Settings
        self.WAIT_AFTER_VPN_CREATE_SECONDS = args.get("WAIT_AFTER_VPN_CREATE_SECONDS",
                                                      5)  # Potentially effective at preventing windows getting confused. Slows testing
        self.NUM_CONCURRENT_VPN_PROCS = args.get("NUM_CONCURRENT_VPN_PROCS",
                                                 7)  # Most stable at 1 core / connection. works fine over, kinda
        self.NUM_PROCS_RESERVED_FOR_SWAPPING = args.get("NUM_PROCS_RESERVED_FOR_SWAPPING",
                                                        0)  # Basically hardcoded right now. Would be nice to implement this functionality
        self.RECYCLE_SESSION_TIMEOUT_SECONDS = args.get("RECYCLE_SESSION_TIMEOUT_SECONDS", 60 * 5)
        self.LIMIT_PER_LOCATION = args.get("LIMIT_PER_LOCATION", -1)  # This would be neat
        self.SESSION_CREATE_FUNCTION = args.get("SESSION_CREATE_FUNCTION",DEFAULT_CREATE_SESSION)
        # runtime variables
        self.dir_list = os.listdir(self.OPENVPN_CONFIG_FOLDER_PATH)
        random.shuffle(self.dir_list)
        self.sessions = {}
        self.PROC_CREATE_INTERVAL_SECONDS = args.get("PROC_CREATE_INTERVAL", 60)
        self.USER_DEFINED_SEND = args.get("USER_DEFINED_GET", None)
        self.USER_DEFINED_RECEIVE = args.get("USER_DEFINED_RECEIVE", None)
        self.proc_create_delay_timer = delay_timer(default_delay_seconds=self.PROC_CREATE_INTERVAL_SECONDS)
        self.URL_BATCH_SIZE = args.get("URL_BATCH_SIZE", 10)
        self.urls_to_send = {}
        self.ips = []

    def get_next_conf(self):  # something better later
        conf = self.dir_list.pop()
        #while conf[:2] != "us": # only us endpoints
        #    conf = self.dir_list.pop()
        return conf

    def list_online_sessions(self):  # blah
        for sess in self.sessions:
            if self.sessions[sess].state == session_state.ONLINE:
                yield sess

    def get_name(self, string):  # get only the location code and server number from the ovpn conf file name
        idx = 0
        got_to_numeric = False
        for c in string:
            idx += 1
            if c.isnumeric() and not got_to_numeric:
                got_to_numeric = True
            elif not c.isnumeric() and got_to_numeric:
                break

        return string[:idx - 1]

    def add_urls_any_session(self, urls):  # add a batch size of urls to each session's outgoing list
        for sess in self.list_online_sessions():
            num_to_add = self.URL_BATCH_SIZE - len(self.urls_to_send[sess])
            if num_to_add > 0:
                self.add_urls_for_session(urls[:num_to_add], sess)
                self.urls_to_send = self.urls_to_send[num_to_add:]
        if urls:
            self.add_urls_all_sessions(urls)

    def add_urls_all_sessions(self, urls):  # evenly distribute all urls
        while urls:
            for sess in self.list_online_sessions():
                if not urls:
                    break
                elif sess in self.urls_to_send:
                    self.urls_to_send[sess].append(urls.pop())
                else:
                    self.urls_to_send[sess] = [urls.pop(), ]

    def add_urls_for_session(self, urls, sess):  # add urls to specific session
        self.urls_to_send[sess] = self.urls_to_send.get(sess, []).extend(urls)

    def get_url_batch(self):  # chunk off a batch of urls
        batch = self.urls_to_send[:self.URL_BATCH_SIZE]
        self.urls_to_send = self.urls_to_send[self.URL_BATCH_SIZE:]
        return batch

    def maintain_sessions(self):  # main function, loops through sessions, recycles dead ones and spawns new ones.
        for i in range(len(self.sessions), self.NUM_CONCURRENT_VPN_PROCS):
            if self.proc_create_delay_timer.ready():
                self.proc_create_delay_timer.delay()
                self.make_new_session()
        kill_list = []
        any_offline = False
        if len(self.sessions) > 0:
            for sess in self.sessions:
                urls = None
                if sess in self.urls_to_send:
                    urls = self.urls_to_send.pop(sess)
                resps = self.sessions[sess].get_responses()
                if resps:
                    for resp in resps:
                        if resp:
                            print(
                                f"[{self.sessions[sess].name}]({resp.status_code}) {resp.request.url} -> {resp.url}: {resp.text[:50]}")
                if self.sessions[sess].state == session_state.ONLINE:
                    if urls:
                        self.sessions[sess].send_reqs(urls)
                else:
                    any_offline = True
                state = self.sessions[sess].check_initialization_process()
                if state == session_state.CONNECTION_TESTED:
                    if self.sessions[sess].remote_ip in self.ips:
                        self.sessions[sess].update_state(session_state.TRAFFIC_MISMATCH)
                    else:
                        self.ips.append(self.sessions[sess].remote_ip)
                if self.sessions[sess].state < -1:
                    kill_list.append(sess)
                    pass
                for sess_string_dic in self.sessions[sess].get_new_status_dics():
                    p.print("session_strings", sess_string_dic)
        for sess in kill_list:
            self.kill_session(sess)
        return not any_offline

    def recycle_session(self, sess):  # convenient for user code
        self.kill_session(sess)
        return self.make_new_session()

    def kill_session(self, sess):
        session = self.sessions.pop(sess)
        if session.remote_ip in self.ips:
            self.ips.remove(session.remote_ip)
        session.terminate()

    def make_new_session(self):
        conf_file = self.get_next_conf()
        name = self.get_name(conf_file)
        #p.print("make_new_session", f"starting {name}")
        sess = TB_SESSION(name,
                          self.OPENVPN_EXE_FULL_PATH,
                          self.OPENVPN_CONFIG_FOLDER_PATH + f"\\{conf_file}",
                          self.OPENVPN_CREDENTIALS_PATH,
                          self.OVPN_CONNECT_RETRY_MAX,
                          self.OVPN_CONNECT_TIMEOUT,
                          self.OVPN_POLL_TIMEOUT,
                          4,
                          self.OVPN_RESOLVE_RETRY,
                          self.SESSION_CLASS_INITIALIZATION_TIMEOUT,
                          self.REQUEST_TIMEOUT,
                          self.INTERNAL_REQUEST_TIMEOUT,
                          DEFAULT_GET_OVPN_COMMAND_STRING,
                          self.USER_DEFINED_SEND,
                          self.USER_DEFINED_RECEIVE,
                          self.SESSION_CREATE_FUNCTION
                          )
        self.sessions[name] = sess
        return name


# ==========================================================
class pp:
    def __init__(self):
        self.profiles = {}

    def add_profile(self, name):
        self.profiles[name] = {
            "columns": []
        }

    def adjust_profile(self, profile, string_list):
        if profile not in self.profiles:
            self.add_profile(profile)
        if len(self.profiles[profile]["columns"]) < len(string_list):
            self.profiles[profile]["columns"].extend([0] * (len(string_list) - len(self.profiles[profile]["columns"])))
        for i, s in enumerate(string_list):
            self.profiles[profile]["columns"][i] = max(len(str(s)), self.profiles[profile]["columns"][i])

    def print_dic(self, profile, dic):
        self.print(profile, [f"[{dic['name']}]", str(dic["state"]), dic["geolocation"], f"remote: {dic['remote']}",
                             dic["local"]])

    def print(self, profile, string):
        if type(string) == str:
            strings = [s.strip() for s in string.split()]
        elif type(string) == list:
            strings = string
        elif type(string) == dict:
            self.print_dic(profile, string)
            return
        else:
            return
        self.adjust_profile(profile, strings)
        for i, s in enumerate(strings):
            string = str(s)
            print(string + " " * (self.profiles[profile]["columns"][i] - len(string) + 1), end='')
        print()


# ==========================================================
class Logger:
    def __init__(self):
        self.path = "C:/Users/justi/PycharmProjects/Traffic_Blaster/logs/"

    def write(self, dir, log):
        if type(log) == bytes:
            log = log.decode()
        if not os.path.exists(self.path + dir):
            os.mkdir(self.path + dir)
        with open(self.path + dir + "/log.txt", 'a') as file:
            file.write(log)


# ==========================================================
class delay_timer:
    def __init__(self, delay_dictionary=None, default_delay_seconds=0):
        self.delay_timer_started = None
        self.default_delay_seconds = default_delay_seconds
        self.active = False
        self.waiting = False
        self.delay_timer_length = timedelta(seconds=0)
        if delay_dictionary is not None:
            self.delay_dictionary = delay_dictionary
            self.delay = self.delay_from_dic
        else:
            self.delay = self.delay_seconds

    def clear(self):
        self.waiting = False
        self.active = False
        self.delay_timer_started = None

    def ready(self):
        if self.waiting and datetime.now() - self.delay_timer_started >= self.delay_timer_length:
            self.waiting = False
        return not self.waiting and self.active

    def delay_seconds(self, seconds=None):
        if seconds is None:
            seconds = self.default_delay_seconds
        self.active = True
        self.waiting = True
        self.delay_timer_started = datetime.now()
        self.delay_timer_length = timedelta(seconds=seconds)

    def delay_from_dic(self, state):
        self.waiting = True
        self.counting_down = True
        self.delay_timer_started = datetime.now()
        if state in self.delay_dictionary:
            self.delay_timer_length = timedelta(seconds=self.delay_dictionary.get(state, self.default_delay_seconds))


def my_recieve_function(session):
    l = [i for i in session.response_list]
    session.response_list.clear()
    return l


def my_send_function(session, urls):
    session.request_list.clear()
    session.request_list.extend([grequests.get(url, session=session.requests_session) for url in urls])
    session.response_list.clear()
    session.response_list.extend(grequests.map(session.request_list, size=0))


session_state = TRAFFIC_BLASTER_SESSION_STATE()
p = pp()
if __name__ == "__main__":
    test_urls = [
        'http://bbc.co.uk', 'http://www.youtube.com', 'http://www.cia.gov', 'http://www.whitehouse.gov',
        'https://www.blogger.com', 'https://www.apple.com',
        'https://www.nih.gov', 'https://www.nytimes.com', 'https://www.twitter.com', 'https://www.twitter.com',
        'https://www.github.com', 'https://www.vimeo.com', 'https://www.cpanel.net',
        'https://www.draft.blogger.com', 'https://www.draft.blogger.com', 'https://www.www.weebly.com',
        'https://www.myspace.com', 'https://www.dan.com',
    ]
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CONFIG_FOLDER_PATH="C:\\Users\\justi\\PycharmProjects\\Traffic_Blaster\\ovpn configs",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        NUM_CONCURRENT_VPN_PROCS=5,
                        OVPN_CONNECT_TIMEOUT=30,
                        PROC_CREATE_INTERVAL=5,
                        SESSION_CLASS_INITIALIZATION_TIMEOUT=60,
                        USER_DEFINED_SEND=my_send_function,
                        USER_DEFINED_RECIEVE=my_recieve_function,

                        )
    while True:
        waiting_to_send = True
        if tb.maintain_sessions() and waiting_to_send:
            waiting_to_send = False
            print("sending...")
            tb.add_urls_all_sessions(test_urls)
