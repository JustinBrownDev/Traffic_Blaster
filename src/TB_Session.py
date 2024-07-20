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
from src.helpers import *
class TB_SESSION:
    def __init__(self, *args):
        self.log = Logger()
        (self.name,
         self.conf_file,  # ...
         self.INITIALIZATION_TIMEOUT,  # Timeout for class to go online
         self.request_timeout,  # Timeout for user side .get requests
         self.internal_request_timeout,  # Timeout for internally used requests (ip & geo)
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
        self.stdout = None
        self.q = None
        self.last_state = self.state
        self.request_list = []
        self.response_list = []
        self.update_state(SESSION_STATE.CLASS_INITIALIZED)
        # Delays
        SESSION_INITIALIZATION_DELAYS = {
            SESSION_STATE.CLASS_INITIALIZED: 0,
            SESSION_STATE.VPN_CONNECTED: 3,
            SESSION_STATE.SESSION_MOUNTED: 10,
            SESSION_STATE.CONNECTION_TESTED: 10,
            SESSION_STATE.ONLINE: 5
        }
        self.initialization_step_timer = delay_timer(delay_dictionary=SESSION_INITIALIZATION_DELAYS,
                                                     start_as_ready=True)
        self.initialization_timeout = delay_timer(default_delay_seconds=self.INITIALIZATION_TIMEOUT)
        self.check_connection_api_delay = delay_timer(default_delay_seconds=30, start_as_ready=True)
        self.down_delay = delay_timer(default_delay_seconds=60)

    # User Code
    def open(self):  #open a process
        if self.ovpn_client_process is not None:  # just in case
            print(f"[{self.name}] ovpn client is already open")
            return None

        # Spawn the OpenVPN subprocess
        command_string = GET_OVPN_COMMAND_STRING()
        self.ovpn_client_process = subprocess.Popen(
            command_string,
            stdout=subprocess.PIPE
        )
        # Outside code #1
        # [https://stackoverflow.com/questions/375427/a-non-blocking-read-on-a-subprocess-pipe-in-python] response by jfs edited by ankostis
        # Thread to continuously read from the subproccess and put outputs on the queue
        self.q = Queue()
        self.stdout = self.ovpn_client_process.stdout

        # Should keep track of this guy !!
        t = Thread(target=ADD_OVPN_OUTPUT_TO_QUEUE, args=(self.stdout, self.q))
        t.daemon = True
        t.start()
        # End of outside code
        # all done, update state, set initialization timeout to start counting down
        self.update_state(SESSION_STATE.VPN_CONNECTING)
        self.initialization_timeout.delay()

    def do_stuff(self):  # main function, called every loop, i really gotta think of a better name
        if self.state == SESSION_STATE.CLASS_INITIALIZED:
            # 1) open() - create OpenVPN process & output queue
            self.open()
            self.update_state(SESSION_STATE.VPN_CONNECTING)
        elif self.initialization_timeout.ready() and not (
                self.state == SESSION_STATE.ONLINE or self.state == SESSION_STATE.CONNECTION_DOWN):
            self.update_state(SESSION_STATE.TIMED_OUT)
        elif self.initialization_step_timer.ready():  # different steps have different delays
            if self.state == SESSION_STATE.VPN_CONNECTING:  # 2) read the pipe until "Initialization sequence completed"
                self.read_pipe()
            elif self.state == SESSION_STATE.VPN_CONNECTED:  # 3) finished, increment state, set delay
                self.initialization_step_timer.delay(self.state)
                self.update_state(SESSION_STATE.MOUNTING_SESSION)
            elif self.state == SESSION_STATE.MOUNTING_SESSION:  # 4) try to mount the new connection with a session
                self.mount_session()
            elif self.state == SESSION_STATE.SESSION_MOUNTED:  # 5) finished, increment state, set delay
                self.initialization_step_timer.delay(self.state)
                self.update_state(SESSION_STATE.TESTING_CONNECTION)
            elif self.state == SESSION_STATE.TESTING_CONNECTION:  # 6) check new session's connection with apis
                self.check_connection()
            elif self.state == SESSION_STATE.CONNECTION_TESTED:  # 7) passed, delay, increment state, clear the timeouts
                self.initialization_step_timer.delay(self.state)
                self.update_state(SESSION_STATE.ONLINE)
                self.initialization_timeout.clear()
                self.down_delay.clear()
            elif self.state == SESSION_STATE.ONLINE:  # 8) online, clear timeouts, check connection
                self.check_connection()
                self.initialization_timeout.clear()
                self.down_delay.clear()
            elif self.state == SESSION_STATE.CONNECTION_DOWN:  # if connection goes offline, start a timeout, keep checking connection
                if self.down_delay.ready():
                    self.update_state(SESSION_STATE.CONNECTION_DOWN_TIMEOUT)
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
        self.status_lists.append({"name": self.name, "state": SESSION_STATE.name(self.state), "remote": self.remote_ip,
                                  "local": self.local_ip, "geolocation": self.geolocation})

    # Check Session
    def check_connection(self):  # get apis to verify IP and Geolocation
        if self.state == SESSION_STATE.TESTING_CONNECTION:  # if on initialization
            ip = self.request_ip_api()
            if not ip:  # assume no response is a connection error on
                self.update_state(SESSION_STATE.CONNECTION_ERROR)
                return
            self.remote_ip = ip
            geolocation = self.request_geo_api()
            if not geolocation:
                self.update_state(SESSION_STATE.CONNECTION_ERROR)
                return
            self.remote_ip = ip
            self.geolocation = geolocation
            self.update_state(SESSION_STATE.CONNECTION_TESTED)
            self.check_connection_api_delay.delay()

        elif self.check_connection_api_delay.ready():  # if routine connection check, and delay is ready
            self.check_connection_api_delay.delay()  # reset delay
            ip = self.request_ip_api()  #
            if not ip:  # no response
                if self.state == SESSION_STATE.ONLINE:  # if we were online, now we're not
                    self.down_delay.delay()
                    self.update_state(SESSION_STATE.CONNECTION_DOWN)
            elif not self.remote_ip == ip:  # wrong ip received, traffic going through wrong endpoint
                self.update_state(SESSION_STATE.TRAFFIC_MISMATCH)
            else:  # all good
                self.update_state(SESSION_STATE.ONLINE)
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
            if "city" in ses_dic_geo:
                return ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
            else:
                print(ses_dic_geo)
                return None

    # Networking
    def mount_session(self):
        self.requests_session = self.SESSION_CREATE_FUNCTION()  # Default is just requests.Session()
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
        self.update_state(SESSION_STATE.SESSION_MOUNTED)
        return True

    # Process Management
    # Outside code #1
    # [https://stackoverflow.com/questions/375427/a-non-blocking-read-on-a-subprocess-pipe-in-python] response by jfs edited by ankostis
    # it just reads from the queue and decodes the output from bytes to string
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
            if line_without_datestr[:33] == "Initialization Sequence Completed" and len(line_without_datestr) <= 35:
                if self.local_ip is None:
                    self.update_state(SESSION_STATE.FAILED_TO_GET_VPN_ADDRESS)
                else:
                    self.update_state(SESSION_STATE.VPN_CONNECTED)
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