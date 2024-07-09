# Justin Brown - 7/8/2024 - Traffic Blaster
import shlex
import asyncio
import os
import sys
import time
import random
import psutil
import requests
import subprocess
from requests_toolbelt.adapters import source
from datetime import datetime, timedelta
# noinspection PyUnresolvedReferences
import traceback
from threading import Thread
from queue import Queue, Empty

ON_POSIX = 'posix' in sys.builtin_module_names


def enqueue_output(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)
    #out.close()


class enum:
    def name(self, num):
        if self.key is None:
            self.key = {tb_session_state.__dict__[i]: i for i in tb_session_state.__dict__ if i[0] != "_"}
        return self.key[num]


class tb_session_state(enum):
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
    key = None




session_state = tb_session_state()


class im_waiting:
    def __init__(self, delay_dictionary = None):
        self.waiting = False
        self.timer_started = datetime.now()
        self.timer_length = timedelta(seconds=0)
        if delay_dictionary is not None:
            self.delay_dictionary = delay_dictionary
            self.delay = self.delay_from_dic
        else:
            self.delay = self.delay_seconds

    def ready(self):
        if self.waiting and datetime.now() - self.timer_started >= self.timer_length:
            self.waiting = False
        return not self.waiting
    def delay_seconds(self,seconds):
        self.waiting = True
        self.timer_started = datetime.now()
        self.timer_length = timedelta(seconds=seconds)
    def delay_from_dic(self, state):
        self.waiting = True
        self.timer_started = datetime.now()
        self.timer_length = timedelta(seconds=self.delay_dictionary[state])


class tb_session:
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
         ) = args
        TB_SESSION_DELAYS = {
            session_state.CLASS_INITIALIZED: 0,
            session_state.VPN_CONNECTED: 3,
            session_state.SESSION_MOUNTED: 10,
            session_state.CONNECTION_TESTED: 10,
            session_state.ONLINE: 5
        }
        self.delay = im_waiting(TB_SESSION_DELAYS)
        self.time_of_last_connection_check = None
        self.ovpn_client_process = None
        self.requests_session = None
        self.time_initialized = datetime.now()
        self.local_ip = None
        self.remote_ip = None
        self.geolocation = None
        self.state = session_state.CLASS_INITIALIZED
        self.stdout = None
        self.q = None
        self.last_print = ""
        self.last_print_time = datetime.now()
        self.delay_start = datetime.now() - timedelta(seconds=7)
        self.last_state = self.state

    # User Code
    def open(self):  #open a process
        if self.ovpn_client_process is not None:
            print("ovpn client is already open")
            return None
        command_string = f"""\"{self.ovpn_exe_path}\" \
--config \"{self.ovpn_config_path}\" --disable-dco --connect-retry-max {self.ovpn_connect_retry_max} \
--connect-timeout {self.ovpn_connect_timeout} \
--server-poll-timeout {self.ovpn_poll_timeout} \
--resolv-retry {self.ovpn_resolve_retry} --auth-user-pass {self.ovpn_credentials_path} \
--verb 4"""
        # --ping {self.OVPN_PING} \
        # --ping-restart {self.OVPN_PING_RESTART} \

        #print(command_string)

        self.ovpn_client_process = subprocess.Popen(
            command_string,
            stdout=subprocess.PIPE, close_fds=ON_POSIX
        )
        self.q = Queue()
        self.stdout = self.ovpn_client_process.stdout
        t = Thread(target=enqueue_output, args=(self.ovpn_client_process.stdout, self.q))
        t.daemon = True
        t.start()
        self.time_initialized = datetime.now()
        self.state = session_state.VPN_CONNECTING

    def check_initialization_process(self):
        print_strings = [f"[{self.name}]", f"({session_state.name(self.state)})",
                f"remote: {self.remote_ip}", f"{self.geolocation}", f"local: {self.local_ip}"""]
        print_str = "".join(print_strings)
        if self.last_print != print_str or datetime.now() - self.last_print_time > timedelta(seconds=5):
            p.print("check_initialization_process", print_strings)
            self.last_print = print_str
            self.last_print_time = datetime.now()
        if self.check_init_timeout():
            self.state = session_state.TIMED_OUT
        if self.delay.ready():
            if self.state == session_state.CLASS_INITIALIZED:
                self.open()
                self.state = session_state.VPN_CONNECTING
            if self.state == session_state.VPN_CONNECTING:
                self.read_pipe()
            elif self.state == session_state.VPN_CONNECTED:
                self.delay.delay(self.state)
                self.state = session_state.MOUNTING_SESSION
            elif self.state == session_state.MOUNTING_SESSION:
                self.mount_session()
            elif self.state == session_state.SESSION_MOUNTED:
                self.delay.delay(self.state)
                self.state = session_state.TESTING_CONNECTION
            elif self.state == session_state.TESTING_CONNECTION:
                self.check_connection()
            elif self.state == session_state.CONNECTION_TESTED:
                self.delay.delay(self.state)
                self.state = session_state.ONLINE
            elif self.state == session_state.ONLINE:
                self.check_connection()
        return self.state

    def get(self, url, timeout=None):
        if timeout is None:
            timeout = self.request_timeout
        return self.requests_session.get(url, timeout=timeout)

    # Check Session
    def check_connection(self):
        if self.state != session_state.ONLINE or datetime.now() - self.time_of_last_connection_check > timedelta(seconds=15):
            self.time_of_last_connection_check = datetime.now()
            if self.remote_ip:
                ip = self.request_ip_api()
                geolocation = self.request_geo_api(self.remote_ip)
                if ip is None or geolocation is None:
                    self.state = session_state.CONNECTION_DOWN
                elif not self.remote_ip == ip or not self.geolocation == geolocation:
                    self.state = session_state.TRAFFIC_MISMATCH
            else:
                ip = self.request_ip_api()
                geolocation = self.request_geo_api(ip)
                if ip is None or geolocation is None:
                    self.state = session_state.CONNECTION_ERROR
                else:
                    self.remote_ip = ip
                    self.geolocation = geolocation
                    self.state = session_state.CONNECTION_TESTED

    def request_ip_api(self):
        try:
            sesresponse_ip = self.get('http://emapp.cc/get_my_ip', timeout=self.internal_request_timeout)
            if sesresponse_ip.status_code != 200:
                success = False
                print(f"ip api: {sesresponse_ip.status_code}")
            ip = sesresponse_ip.text
            ses_dic_ip = eval(ip)
            ip = ses_dic_ip["result_data"]
            return ip
        except Exception:
            self.log.write("check_connection",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
            print(f"failed to connect to session IP api : {self.remote_ip}")

            return None

    def set_remote_ip(self, ip):
        self.remote_ip = ip

    def set_geolocation(self, geo):
        self.geolocation = geo

    def request_geo_api(self, ip=None):
        if ip is None:
            ip = self.remote_ip
        try:
            sesresponse_geo = self.get(f"http://ip-api.com/json/{ip}",
                                       timeout=self.internal_request_timeout)
            if sesresponse_geo.status_code != 200:
                success = False
                print(f"geo api: {sesresponse_geo.status_code}")
            item_geo = sesresponse_geo.text
            if item_geo:
                ses_dic_geo = eval(item_geo)
                item_geo = ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
                return item_geo
        except Exception:
            self.log.write("check_connection",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
            print(f"failed to connect to session geolocation api {self.geolocation}")
            return None

    # Networking
    def mount_session(self):
        self.requests_session = requests.session()
        try:
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
            # session.mount('http://', iface)
            # session.mount('https://', iface)
            self.state = session_state.SESSION_MOUNTED
            return True
        except:
            print(f"failed to mount {self.local_ip}")
            self.state = session_state.FAILED_TO_MOUNT
            return False

    # OpenVpn response checking

    # Process Management
    def check_init_timeout(self):
        return not (self.state == session_state.ONLINE) and datetime.now() - self.time_initialized > timedelta(
            seconds=self.timeout)

    def read_pipe(self):
        try:
            line = self.q.get_nowait().decode()  # or q.get(timeout=.1)
        except Empty:
            return
            #print('no output yet')

        if line[30:63] == "Initialization Sequence Completed" and "Error" not in line:
            if self.local_ip == None:
                self.state = session_state.FAILED_TO_GET_VPN_ADDRESS
            else:
                self.state = session_state.VPN_CONNECTED
        elif line[30:83] == 'Set TAP-Windows TUN subnet mode network/local/netmask':
            i = 96
            for c in line[96:]:
                if c.isnumeric() or c == '.':
                    i += 1
                else:
                    break
            self.local_ip = line[96:i]


    def recycle(self):
        pass

    def terminate(self):
        if self.requests_session:
            self.requests_session.close()
        self.ovpn_client_process.terminate()


def get_interfaces():  # get interfaces that look like vpn tunnels
    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    iface_addrs = []
    for intface, addr_list in addresses.items():
        if any(getattr(addr, 'address').startswith("169.254") for addr in addr_list):
            continue
        elif intface in stats and getattr(stats[intface], "isup"):
            for addr in addr_list:
                if str(addr.address).startswith("10.9"):
                    iface_addrs.append(addr.address)
    return iface_addrs


class TrafficBlaster:
    def __init__(self, **args):

        # Class Declarations ==========
        self.log = Logger()  # <--- Replace me with something better please

        self.delay = im_waiting()
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
        # Traffic Blaster Settings
        self.WAIT_AFTER_VPN_CREATE_SECONDS = args.get("WAIT_AFTER_VPN_CREATE_SECONDS",
                                                      5)  # Potentially effective at preventing windows getting confused. Slows testing
        self.NUM_CONCURRENT_VPN_PROCS = args.get("NUM_CONCURRENT_VPN_PROCS",
                                                 7)  # Most stable at 1 core / connection. works fine over, kinda
        self.NUM_PROCS_RESERVED_FOR_SWAPPING = args.get("NUM_PROCS_RESERVED_FOR_SWAPPING",
                                                        0)  # Basically hardcoded right now. Would be nice to implement this functionality
        self.RECYCLE_SESSION_TIMEOUT_SECONDS = args.get("RECYCLE_SESSION_TIMEOUT_SECONDS", 60 * 5)
        self.LIMIT_PER_LOCATION = args.get("LIMIT_PER_LOCATION", -1)  # This would be neat
        # runtime variables
        self.dir_list = os.listdir(self.OPENVPN_CONFIG_FOLDER_PATH)
        random.shuffle(self.dir_list)
        self.sessions = {}
        self.PROC_CREATE_INTERVAL = timedelta(seconds=args.get("PROC_CREATE_INTERVAL", 60))
        self.time_since_last_proc = datetime.now() - self.PROC_CREATE_INTERVAL
        self.fully_initialized = False
    def get_next_conf(self):  # something better later
        conf = self.dir_list.pop()
        while conf[:2] != "us":
            conf = self.dir_list.pop()
        return conf

    def get_name(self, string):
        idx = 0
        got_to_numeric = False
        for c in string:
            idx += 1
            if c.isnumeric() and not got_to_numeric:
                got_to_numeric = True
            elif not c.isnumeric() and got_to_numeric:
                break


        return string[:idx-1]

    def maintain_sessions(self):
        all_set = False
        #SRCADDRS = []
        while not all_set:
            if not self.fully_initialized or self.delay.ready():
                for i in range(len(self.sessions), self.NUM_CONCURRENT_VPN_PROCS):
                    if datetime.now() - self.time_since_last_proc > self.PROC_CREATE_INTERVAL:
                        self.make_new_session()
                        self.time_since_last_proc = datetime.now()
                kill_list = []
                if len(self.sessions) > 0:
                    all_set = True
                    for sess in self.sessions:
                        #print(session_state.name(self.sessions[sess].state))
                        self.sessions[sess].check_initialization_process()
                        if self.sessions[sess].state != session_state.ONLINE:
                            all_set = False
                        if self.sessions[sess].state < 0:
                            kill_list.append(sess)
                            pass
                for sess in kill_list:
                    self.kill_session(sess)
                if self.fully_initialized:
                    self.delay.delay(10)
                elif all_set and len(self.sessions) < self.NUM_CONCURRENT_VPN_PROCS:
                    all_set = False
                    self.time_since_last_proc = self.time_since_last_proc - self.PROC_CREATE_INTERVAL
        self.fully_initialized = True
    def recycle_session(self, sess):
        self.kill_session(sess)
        return self.make_new_session()

    def kill_session(self, sess):
        session = self.sessions.pop(sess)
        session.terminate()

    def make_new_session(self):
        conf_file = self.get_next_conf()
        name = self.get_name(conf_file)
        #p.print("make_new_session", f"starting {name}")
        sess = tb_session(name,
                          self.OPENVPN_EXE_FULL_PATH,
                          self.OPENVPN_CONFIG_FOLDER_PATH + f"\\{conf_file}",
                          self.OPENVPN_CREDENTIALS_PATH,
                          self.OVPN_CONNECT_RETRY_MAX,
                          self.OVPN_CONNECT_TIMEOUT,
                          self.OVPN_POLL_TIMEOUT,
                          4,
                          self.OVPN_RESOLVE_RETRY,
                          self.OVPN_CONNECT_TIMEOUT,
                          self.REQUEST_TIMEOUT,
                          self.INTERNAL_REQUEST_TIMEOUT)
        self.sessions[name] = sess
        return name


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

    def print(self, profile, string):
        if type(string) == str:
            strings = [s.strip() for s in string.split()]
        elif type(string) == list:
            strings = string
        else:
            return
        self.adjust_profile(profile, strings)
        for i, s in enumerate(strings):
            string = str(s)
            print(string + " " * (self.profiles[profile]["columns"][i] - len(string) + 1), end='')
        print()


p = pp()


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


# map filter


def timer(target, args):
    start = datetime.now()
    value = target(*args)
    delta = (datetime.now() - start).seconds
    return value, delta


if __name__ == "__main__":
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CONFIG_FOLDER_PATH="C:\\Users\\justi\\PycharmProjects\\Traffic_Blaster\\ovpn configs",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        NUM_CONCURRENT_VPN_PROCS=10,
                        OVPN_CONNECT_TIMEOUT=30,
                        PROC_CREATE_INTERVAL=15
                        )
    while True:
        tb.maintain_sessions()
        print("Yay!")

