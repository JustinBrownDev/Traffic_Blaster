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
    TIMED_OUT = -1
    TRAFFIC_MISMATCH = -2
    CONNECTION_ERROR = -3
    CONNECTION_DOWN = -4



state = initialization_state()


class tb_session:
    def __init__(self, args):
        (self.ovpn_exe_path,
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
        pass


class TrafficBlaster:
    def __init__(self, **args):

        # Class Declarations ==========
        self.p = pp()
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
        # Traffic Blaster Settings
        self.WAIT_AFTER_VPN_CREATE_SECONDS = args.get("WAIT_AFTER_VPN_CREATE_SECONDS",
                                                      5)  # Potentially effective at preventing windows getting confused. Slows testing
        self.NUM_CONCURRENT_VPN_PROCS = args.get("NUM_CONCURRENT_VPN_PROCS",
                                                 7)  # Most stable at 1 core / connection. works fine over, kinda
        self.NUM_PROCS_RESERVED_FOR_SWAPPING = args.get("NUM_PROCS_RESERVED_FOR_SWAPPING",
                                                        0)  # Basically hardcoded right now. Would be nice to implement this functionality
        self.RECYCLE_SESSION_TIMEOUT_SECONDS = args.get("RECYCLE_SESSION_TIMEOUT_SECONDS", 60 * 5)
        self.LIMIT_PER_LOCATION = args.get("LIMIT_PER_LOCATION", -1)  # This would be neat

        # RUNTIME VARS ==============
        self.response_dictionary = {"Initialization Sequence Completed\n": 1,
                                    "TLS Error: TLS key negotiation failed to occur within 60 seconds": -1,
                                    "SIGTERM[soft,auth-failure] received, process exiting": -5}
        self.ovpn_err_keys = list(self.response_dictionary.keys())
        self.dir_list = os.listdir("C:\\Users\\justi\\PycharmProjects\\Traffic_Blaster\\ovpn configs")
        random.shuffle(self.dir_list)
        self.addr_iface_dic = {}
        self.start_ifaces = self.get_interfaces()

        self.iface_list = []  # List of network interfaces - We only know which connection is which by noticing when a new one arrives
        self.sessions = {}  # Active sessions
        self.succ_count = 0  # Used for counting sessions on startup
        self.reserve_sessions = {}
        self.session_constructor_function = args.get("session_constructor_function",
                                                     self.default_session_constructor_function)

    def default_session_constructor_function(self):
        return requests.session()

    def get_interfaces(self):  # get interfaces that look like vpn tunnels
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
                        self.addr_iface_dic[addr.address] = intface
        return iface_addrs

    def default_requests_single_get_function(self, session, url):
        return session.get(url)

    def initialize_vpn_proc(self, item):  # initialize an openvpn client process
        command_string = f"""\"{self.OPENVPN_EXE_FULL_PATH}\" \
--config \"{self.OPENVPN_CONFIG_FOLDER_PATH}/{item}\" \
--auth-user-pass {self.OPENVPN_CREDENTIALS_PATH} \
--disable-dco --connect-retry-max {self.OVPN_CONNECT_RETRY_MAX} \
--connect-timeout {self.OVPN_CONNECT_TIMEOUT} \
--server-poll-timeout {self.OVPN_POLL_TIMEOUT} \
--verb 4 \
--resolv-retry {self.OVPN_RESOLVE_RETRY}"""
        # --ping {self.OVPN_PING} \
        # --ping-restart {self.OVPN_PING_RESTART} \

        # print(command_string)
        proc = subprocess.Popen(
            command_string,
            stdout=subprocess.PIPE, )
        status = 0
        start = datetime.now()
        delta = datetime.now() - start
        self.log.write(f"ovpn/{item}", f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-")
        while -5 < status < 1 and delta < timedelta(seconds=self.CREATE_VPN_TIMEOUT_SECONDS):
            delta = datetime.now() - start
            print(f"[{self.CREATE_VPN_TIMEOUT_SECONDS - delta.seconds}] connecting to {item}          \t ", end='\r')
            line = proc.stdout.readline()
            str_line = line.decode()
            self.log.write(f"ovpn/{item}", str_line[:-1])
            for key in self.ovpn_err_keys:
                if key in str_line:
                    s = self.response_dictionary[key]
                    if s == 1:
                        status = 1
                        break
                    else:
                        status += s
                    # print(f"status:{response_dictionary[key]}")
            if status == 1 or status <= -5:
                break
        return status, proc

    def get_session_info(self, session, name):  # takes a connected session and verifies vpn ip & geolocation
        item_ip = ""
        item_geo = ""
        success = True
        try:
            sesresponse_ip = session.get('http://emapp.cc/get_my_ip', timeout=30)
            if sesresponse_ip.status_code != 200:
                success = False
                print(f"ip api: {sesresponse_ip.status_code}")
            item_ip = sesresponse_ip.text
            ses_dic_ip = eval(item_ip)
            item_ip = ses_dic_ip["result_data"]
            if (name not in self.sessions and item_ip in [self.sessions[i]["ip"] for i in self.sessions]) or (
                    name in self.sessions and not self.sessions[name]["ip"] == item_ip):
                success = False
                print(f"traffic not flowing through correct interface ({item_ip})")
        except Exception:
            print(f"failed to connect to session IP api {item_ip}")
            self.log.write("get_session_info",
                           f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
            return None
        if success:
            try:
                sesresponse_geo = session.get(f"http://ip-api.com/json/{item_ip}", timeout=30)
                if sesresponse_geo.status_code != 200:
                    success = False
                    print(f"geo api: {sesresponse_geo.status_code}")
                item_geo = sesresponse_geo.text
                ses_dic_geo = eval(item_geo)
                item_geo = ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
                # if item_geo in [self.sessions[i]["geo"] for i in self.sessions]:
                # success = False
                # print("")
            except Exception:
                print(f"failed to connect to session geolocation api {item_geo}")
                self.log.write("get_session_info",
                               f"\n\n  -{datetime.now().strftime('%H:%M | %S')}-\n{traceback.format_exc()}")
                return None
        if success:
            return {"session": session, "ip": item_ip, "geo": item_geo}
        return None

    def create_session(self, ovpn_config, session=None):  # takes ovpn_config file name and tries to spin up a session
        print(f" connecting to {ovpn_config}", end='\r')
        start = datetime.now()
        status, proc = self.initialize_vpn_proc(ovpn_config)
        delta = datetime.now() - start
        if status == 1:
            print("                                                                              ", end='\r')
            print(f"""[{self.succ_count + 1}/{self.NUM_CONCURRENT_VPN_PROCS}]\
                {ovpn_config}  ({delta.seconds}s) \t\t """, end='')
            start = datetime.now()
            addr, session, interface = self.mount_new_interface()
            if session is not None:
                session_info = self.get_session_info(session, ovpn_config)
                delta = datetime.now() - start
                if session_info is not None:
                    session_info["interface"] = interface
                    session_info["process"] = proc
                    session_info["addr"] = addr
                    session_info["alive"] = True
                    session_info["last_up_datetime"] = datetime.now()
                    print(f" {session_info['geo']} : {session_info['ip']} ({delta.seconds}s)")
                    self.succ_count += 1
                    return session_info
                else:
                    print("failed to get session")
                    proc.terminate()
                    return None
            else:
                print(f"failed to get session")
                proc.terminate()
                return None
        else:
            print(f"[{status}] {ovpn_config} failed ({delta.seconds}s)                                 ")
            proc.terminate()
            return None

    def read_procs(self):
        for session in self.sessions:
            proc = self.sessions[session]["process"]
            line = proc.stdout.readline()
            while line is not None:
                str_line = line.decode()
                self.log.write(f"ovpn/{session}", str_line[:-1])

    def mount_new_interface(self):  # look for a newly spawned interface, add it to iface_list, and try to mount it
        session = self.session_constructor_function()
        ifaces_addrs = self.get_interfaces()
        interface = None
        addr = None
        for addr in ifaces_addrs:
            if addr not in self.start_ifaces and addr not in self.iface_list:
                self.iface_list.append(addr)
                try:
                    iface = source.SourceAddressAdapter(addr, pool_block=True)
                    session.get_adapter('http://').init_poolmanager(
                        # those are default values from HTTPAdapter's constructor
                        connections=requests.adapters.DEFAULT_POOLSIZE,
                        maxsize=requests.adapters.DEFAULT_POOLSIZE,
                        # This should be a tuple of (address, port). Port 0 means auto-selection.
                        source_address=(addr, 0),
                    )
                    session.get_adapter('https://').init_poolmanager(
                        # those are default values from HTTPAdapter's constructor
                        connections=requests.adapters.DEFAULT_POOLSIZE,
                        maxsize=requests.adapters.DEFAULT_POOLSIZE,
                        # This should be a tuple of (address, port). Port 0 means auto-selection.
                        source_address=(addr, 0),
                    )
                    # session.mount('http://', iface)
                    # session.mount('https://', iface)
                    interface = iface
                except:
                    print(f"failed to mount {addr}")
                    session = None
                if session is not None:
                    break
        return addr, session, interface

    def init_max_sessions(self):  # initializes sessions until it reaches the maximum allowed amount
        while self.succ_count < self.NUM_CONCURRENT_VPN_PROCS:
            item = self.get_next_conf()
            session = self.create_session(item)
            if session is not None:
                self.sessions[item] = session
            for seconds in range(self.WAIT_AFTER_VPN_CREATE_SECONDS):
                print(f" waiting {self.WAIT_AFTER_VPN_CREATE_SECONDS - seconds} seconds", end='\r')
                time.sleep(1)
        while self.succ_count < self.NUM_PROCS_RESERVED_FOR_SWAPPING + self.NUM_CONCURRENT_VPN_PROCS:
            item = self.get_next_conf()
            session = self.create_session(item)
            if session is not None:
                self.reserve_sessions[item] = session
            for seconds in range(self.WAIT_AFTER_VPN_CREATE_SECONDS):
                print(f" waiting {self.WAIT_AFTER_VPN_CREATE_SECONDS - seconds} seconds", end='\r')
                time.sleep(1)
        self.succ_count = 0
        print(f"{self.NUM_CONCURRENT_VPN_PROCS} + {self.NUM_PROCS_RESERVED_FOR_SWAPPING} servers initialized")

    def get_sessions_list(self):
        return [self.sessions[i]['session'] for i in self.sessions]

    def check_sessions(self,
                       sessions=None):  # passes sessions back to get_session_info to verify they're still connected
        if sessions is None:
            sessions = self.sessions
        sessions_to_swap = []
        for session in sessions:
            # self.remount_interface(session)
            sess_is_up = self.check_interface_is_up(sessions[session]["addr"])
            ses_info = self.get_session_info(sessions[session]["session"], session)
            if ses_info is None:
                print(
                    f"""[{sess_is_up}] error getting sess info for {sessions[session]['ip']} {sessions[session]['geo']}\
                                                [{(datetime.now() - sessions[session]['last_up_datetime']).seconds}s]""")
                if datetime.now() - sessions[session]["last_up_datetime"] > timedelta(
                        seconds=self.RECYCLE_SESSION_TIMEOUT_SECONDS):
                    sessions_to_swap.append(session)
            else:
                self.p.print("ONLINE", session, ses_info['ip'], ses_info['geo'], "ONLINE")
                sessions[session]["last_up_datetime"] = datetime.now()
        for session in sessions_to_swap:
            self.swap_session(session)

    def check_interface_is_up(self, addr):
        stats = psutil.net_if_stats()
        interface = self.addr_iface_dic[addr]
        if interface in stats and getattr(stats[interface], "isup"):
            return True
        else:
            return False

    def get_next_conf(self):  # something better later
        return self.dir_list.pop()

    def swap_session(self, session_key):  # kill a session, replace with a new one
        print(f"terminating {session_key}")
        self.sessions[session_key]["alive"] = False
        new_session_key = None
        if self.NUM_PROCS_RESERVED_FOR_SWAPPING <= 0:
            session = None
            while session is None:
                session_conf = self.get_next_conf()
                session = self.create_session(session_conf)
                for seconds in range(self.WAIT_AFTER_VPN_CREATE_SECONDS):
                    print(f" waiting {self.WAIT_AFTER_VPN_CREATE_SECONDS - seconds} seconds", end='\r')
                    time.sleep(1)
            self.reserve_sessions[session_conf] = session
        for new_session_key in self.reserve_sessions:
            if self.reserve_sessions[new_session_key]["alive"]:
                break
        new_session = self.reserve_sessions.pop(new_session_key)
        self.sessions[new_session_key] = new_session
        old_session = self.sessions.pop(session_key)
        old_session["process"].terminate()
        self.addr_iface_dic.pop(old_session["addr"])
        self.iface_list.remove(old_session["addr"])
        session = None
        session_conf = None
        if self.NUM_PROCS_RESERVED_FOR_SWAPPING > 0:
            while session is None:
                session_conf = self.get_next_conf()
                session = self.create_session(session_conf)
                for seconds in range(self.WAIT_AFTER_VPN_CREATE_SECONDS):
                    print(f" waiting {self.WAIT_AFTER_VPN_CREATE_SECONDS - seconds} seconds", end='\r')
                    time.sleep(1)
            self.reserve_sessions[session_conf] = session

    def terminate_all_sessions(self):  # ...
        pass


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

    def print(self, profile, *strings):
        self.adjust_profile(profile, strings)
        for i, s in enumerate(strings):
            string = str(s)
            print(string + " " * (self.profiles[profile]["columns"][i] - len(string) + 1), end='')
        print()


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
