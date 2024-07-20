import requests
import os
from datetime import datetime, timedelta

GET_OVPN_COMMAND_STRING = None # supress error before its defined

def ADD_OVPN_OUTPUT_TO_QUEUE(out, queue):  # threaded function that takes the process output and adds it to the queue
    for line in iter(out.readline, b''):
        queue.put(line)


def DEFAULT_CREATE_SESSION():
    return requests.session()


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


p = pp()


class Logger:
    def __init__(self):
        self.path = "../testing/logs/"

    def write(self, dir, log):
        if type(log) == bytes:
            log = log.decode()
        if not os.path.exists(self.path + dir):
            os.mkdir(self.path + dir)
        with open(self.path + dir + "/log.txt", 'a') as file:
            file.write(log)


class delay_timer:
    def __init__(self, delay_dictionary=None, default_delay_seconds=0, start_as_ready=None):
        self.delay_timer_started = None
        self.default_delay_seconds = default_delay_seconds
        self.active = False
        if start_as_ready:
            self.active = True
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


SESSION_STATE = TRAFFIC_BLASTER_SESSION_STATE()
