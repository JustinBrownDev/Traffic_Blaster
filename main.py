# Justin Brown - 7/8/2024 - Traffic Blaster
# TO DO:
#
import asyncio
import os
import time
import random
import psutil
import requests
import subprocess
from requests_toolbelt.adapters import source
from datetime import datetime, timedelta
# noinspection PyUnresolvedReferences
import traceback


class initialization_state:
    CLASS_INITIALIZED = 0
    VPN_CONNECTING = 1
    VPN_SUCCESS = 2
    TESTING_CONNECTION = 3
    ONLINE = 4
    CONNECTION_DOWN = -1
    TRAFFIC_MISMATCH = -2
    CONNECTION_ERROR = -3
    TIMED_OUT = -4


state_dict = {initialization_state.__dict__[i]: i for i in initialization_state.__dict__ if i[0] != "_"}

state = initialization_state()


class tb_session:
    def __init__(self, *args):
        (self.name,
         self.ovpn_exe_path,
         self.ovpn_config_path,
         self.ovpn_credentials_path,
         self.ovpn_connect_retry_max,
         self.ovpn_connect_timeout,
         self.ovpn_poll_timeout,
         self.ovpn_verbosity,
         self.ovpn_resolve_retry,
         self.timeout,
         self.request_timeout,
         self.internal_request_timeout,
         ) = args
        self.ovpn_client_process = None
        self.requests_session = None
        self.time_initialized = None
        self.local_ip = None
        self.remote_ip = None
        self.geolocation = None
        self.state = state.CLASS_INITIALIZED

    # User Code
    def open(self):  #open a process
        if self.ovpn_client_process is not None:
            print("ovpn client is already open")
            return None
        command_string = f"""--config \"{self.ovpn_config_path}" \
        --auth-user-pass {self.ovpn_credentials_path} \
        --disable-dco --connect-retry-max {self.ovpn_connect_retry_max} \
        --connect-timeout {self.ovpn_connect_timeout} \
        --server-poll-timeout {self.ovpn_poll_timeout} \
        --resolv-retry {self.ovpn_resolve_retry}"""
        # --ping {self.OVPN_PING} \
        # --ping-restart {self.OVPN_PING_RESTART} \

        # print(command_string)

        self.ovpn_client_process = subprocess.Popen(
            command_string,
            stdout=subprocess.PIPE)
        self.time_initialized = datetime.now()
        self.state = state.VPN_CONNECTING

    def check_initialization_process(self):
        if self.check_init_timeout():
            self.state = state.TIMED_OUT
        elif self.state == state.VPN_CONNECTING:
            self.read_pipe()
        elif self.state == state.VPN_SUCCESS:
            self.check_connection()
        return self.state

    def get(self, url, timeout=None):
        if timeout is None:
            timeout = self.request_timeout
        return self.requests_session.get(url, timeout=timeout)

    # Check Session
    def check_connection(self):
        ip = self.request_ip_api()
        geolocation = self.request_geo_api(self.local_ip)
        if self.remote_ip:
            if ip is None or geolocation is None:
                self.state = state.CONNECTION_DOWN
            elif not self.remote_ip == ip or not self.geolocation == geolocation:
                self.state = state.TRAFFIC_MISMATCH
            else:
                self.state = state.ONLINE
        else:
            if ip is None or geolocation is None:
                self.state = state.CONNECTION_ERROR
            else:
                self.remote_ip = ip
                self.geolocation = geolocation
        p.print("check connection", f"""[{self.name}] ({state_dict[self.state]}) \
remote: {self.remote_ip} {self.geolocation} local: {self.local_ip}""")

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
            print(f"failed to connect to session IP api : {ip}")
            self.log.write("get_session_info",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
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
            ses_dic_geo = eval(item_geo)
            item_geo = ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
            return item_geo
        except Exception:
            print(f"failed to connect to session geolocation api {item_geo}")
            self.log.write("get_session_info",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
            return None

    # Networking
    def mount_session(self):
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
            return True
        except:
            print(f"failed to mount {self.local_ip}")
            return False

    # OpenVpn response checking

    # Process Management
    def check_init_timeout(self):
        return not (self.state == state.ONLINE) and datetime.now() - self.time_initialized > timedelta(
            seconds=self.timeout)

    def read_pipe(self):
        line = self.ovpn_client_process.stdout.readline()
        while line is not None:
            str_line = line.decode()
            if str_line[29:63] == "Initialization Sequence Completed\n":
                self.client_online = True
            elif str_line[30:83] == 'Set TAP-Windows TUN subnet mode network/local/netmask':
                self.local_ip = str_line[96:106]
            line = self.ovpn_client_process.stdout.readline()

    def recycle(self):
        pass

    def terminate(self):
        self.requests_session.close()
        self.ovpn_client_process.terminate()


class TrafficBlaster:
    def __init__(self, **args):

        # Class Declarations ==========
        self.log = Logger()  # <--- Replace me with something better please

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
                                             30)  # probably crank this down to like 10 and see what happens, it does not respect me
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

    def get_next_conf(self):  # something better later
        return self.dir_list.pop()

    def get_name(self, string):
        idx = 0
        for c in string:
            idx += 1
            if c.isNumeric():
                break
        return string[:idx]

    def init_max_sessions(self):
        all_set = False
        while not all_set:
            for i in range(len(self.sessions), self.NUM_CONCURRENT_VPN_PROCS):
                self.make_new_session()
            kill_list = []
            for sess in self.sessions:
                all_set = True
                self.sessions[sess].check_initialization_process()
                if sess.state != state.ONLINE:
                    all_set = False
                if sess.state < 0:
                    kill_list.append(sess)
            for sess in kill_list:
                self.kill_session(sess)

    def recycle_session(self, sess):
        self.kill_session(sess)
        return self.make_new_session()

    def kill_session(self, sess):
        session = self.sessions.pop(sess)
        session.terminate()

    def make_new_session(self):
        conf_file = self.get_next_conf()
        name = self.get_name(conf_file)
        sess = tb_session(name,
                          self.OPENVPN_EXE_FULL_PATH,
                          self.OPENVPN_CONFIG_FOLDER_PATH + f"/{conf_file}",
                          self.OPENVPN_CREDENTIALS_PATH,
                          self.OVPN_CONNECT_RETRY_MAX,
                          self.OVPN_CONNECT_TIMEOUT,
                          self.OVPN_POLL_TIMEOUT,
                          4,
                          self.OVPN_RESOLVE_RETRY,
                          self.OVPN_CONNECT_TIMEOUT,
                          self.REQUEST_TIMEOUT,
                          self.INTERNAL_REQUEST_TIMEOUT)
        sess.open()

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
        if len(self.profiles[profile]["columns"]) < len(string_list):
            self.profiles[profile]["columns"].extend([0] * (len(string_list) - len(self.profiles[profile]["columns"])))
        for i, s in enumerate(string_list):
            self.profiles[profile]["columns"][i] = max(len(str(s)), self.profiles[profile]["columns"][i])

    def print(self, profile, string):
        strings = [s.strip() for s in string.split()]
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
                        OPENVPN_CONFIG_FOLDER_PATH="C:/Users/justi/PycharmProjects/Traffic_Blaster/ovpn configs",
                        OPENVPN_CREDENTIALS_PATH="C:/Users/justi/Documents/ovpncreds.txt")
    tb.init_max_sessions()
    tb.check_sessions()
    start = datetime.now()
    while True:
        if datetime.now() - start > timedelta(seconds=60):
            print("\n")
            # tb.read_procs()
            tb.check_sessions()
            start = datetime.now()
        else:
            print(f" {60 - (datetime.now() - start).seconds}    ", end='\r')
