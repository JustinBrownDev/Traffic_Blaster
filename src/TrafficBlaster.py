# Justin Brown - 7/8/2024 - Traffic Blaster
import random
from src.TB_Session import *


class TrafficBlaster:
    def __init__(self, **args):
        self.LOG_DIR = args.get("LOG_DIR", os.getcwd() + "\\logs\\")
        if not (args.get("OPENVPN_EXE_FULL_PATH") and args.get("OPENVPN_CONFIG_FOLDER_PATH")):  # supplying these are mandatory
            print(
                "you must supply at least the path to an openvpn.exe file, and a path to a directory with .ovpn configuration files")
            print("ex:\n\tTrafficBlaster(OPENVPN_EXE_FULL_PATH = ..., OPENVPN_CONFIG_FOLDER_PATH = ...)")
        else:
            self.OPENVPN_EXE_FULL_PATH = args.get("OPENVPN_EXE_FULL_PATH")
            if not os.path.exists(self.OPENVPN_EXE_FULL_PATH):  # Check EXE path
                if not os.path.exists(os.path.realpath(self.OPENVPN_EXE_FULL_PATH)):
                    print("bad exe path ")  # !! needs something better than printing
                else:
                    self.OPENVPN_EXE_FULL_PATH = os.path.realpath(self.OPENVPN_EXE_FULL_PATH)

            self.CONFIG_SELECT_FUNCTION = args.get("CONFIG_SELECT_FUNCTION")
            self.OPENVPN_CONFIG_FOLDER_PATH = args.get("OPENVPN_CONFIG_FOLDER_PATH")
            if not os.path.exists(self.OPENVPN_CONFIG_FOLDER_PATH) and not self.CONFIG_SELECT_FUNCTION:  # Check Config
                if not os.path.exists(os.path.realpath(self.OPENVPN_CONFIG_FOLDER_PATH)):
                    print("bad config folder path ")  # !! needs something better than printing
                else:
                    self.OPENVPN_EXE_FULL_PATH = os.path.realpath(self.OPENVPN_CONFIG_FOLDER_PATH)
            if self.CONFIG_SELECT_FUNCTION is None:
                self.get_next_conf = self.default_get_next_conf
            else:
                self.get_next_conf = self.CONFIG_SELECT_FUNCTION

            self.OPENVPN_CREDENTIALS_PATH = args.get("OPENVPN_CREDENTIALS_PATH")

            self.OPENVPN_ARGS = args.get("OPENVPN_ARGS",
                                         {})  # extra arguments the user can pass to the openvpn cli command
            # credentials path/auth-user-pass kinda belongs here, but it's too important

            if self.OPENVPN_CREDENTIALS_PATH is not None:  # check credentials path, same path check as above
                if not os.path.exists(self.OPENVPN_CREDENTIALS_PATH):  # Check Credentials
                    if not os.path.exists(os.path.realpath(self.OPENVPN_CREDENTIALS_PATH)):
                        print("bad credentials path ")  # !! needs something better than printing
                    else:
                        self.OPENVPN_CREDENTIALS_PATH = os.path.realpath(self.OPENVPN_CREDENTIALS_PATH)

                self.OPENVPN_ARGS[
                    "auth-user-pass"] = self.OPENVPN_CREDENTIALS_PATH  # tag the credentials argument onto here

            self.additional_args_string = ""  # format arguments into valid cli arguments string
            for arg in self.OPENVPN_ARGS:
                self.additional_args_string += f"--{arg} "  #--arg-name 'value' format
                value = self.OPENVPN_ARGS.get(arg)
                if value is not None:  # None value used to denote an argument with no value ex: --disable-dco
                    self.additional_args_string += f"{value} "

        # Timeouts
        self.INTERNAL_REQUEST_TIMEOUT = args.get("INTERNAL_REQUEST_TIMEOUT", 60)  # for apis
        self.SESSION_CLASS_INITIALIZATION_TIMEOUT = args.get("SESSION_CLASS_INITIALIZATION_TIMEOUT",
                                                             75)  # how long a session has to get online before being recycled
        self.RECYCLE_SESSION_TIMEOUT_SECONDS = args.get("RECYCLE_SESSION_TIMEOUT_SECONDS",
                                                        60 * 2)  # how long a session has to get BACK online after going down before being recycled
        # Delays
        self.PROC_CREATE_INTERVAL_SECONDS = args.get("PROC_CREATE_INTERVAL", 15) # how quickly to create new sessions
        self.proc_create_interval_timer = delay_timer(default_delay_seconds=self.PROC_CREATE_INTERVAL_SECONDS,
                                                      start_as_ready=True)

        # Traffic Blaster Settings
        self.NUM_CONCURRENT_VPN_PROCS = args.get("NUM_CONCURRENT_VPN_PROCS",
                                                 3)  # Most stable at 1 core / connection. works fine over, kinda

        self.SESSION_CREATE_FUNCTION = args.get("SESSION_CREATE_FUNCTION",
                                                DEFAULT_CREATE_SESSION)  # function that returns fresh requests sessions, or classes
        # derived from requests.session, would this also just work if the user supplied the class? i.e. SESSION_CREATE_FUNCTION = requests.Session ?


        # runtime variables
        self.dir_list = os.listdir(self.OPENVPN_CONFIG_FOLDER_PATH) # list of ovpn config files
        random.shuffle(self.dir_list)                               # shuffle them so we get different ones

        self.sessions = {}                                          # dic that holds our session objects
        self.ips = []                                               # list that holds the currently used remote ips, so we can check for duplicates
        self.vpn_tracker = vpn_tracker(self.LOG_DIR)                # logging class for the sessions

    def recycle_session(self, sess):  # convenient for user code
        self.kill_session(sess)
        return self.make_new_session()

    def kill_session(self, sess):    # what is says
        session = self.sessions.pop(sess) # pop it from self.sessions
        if session.remote_ip in self.ips: # if we got a remote ip, remove the remote ip from self.ips
            self.ips.remove(session.remote_ip)
        session.terminate() # terminate the process

    def make_new_session(self) -> TB_SESSION:
        conf_file = self.get_next_conf() # get a ovpn config file, create the command string here so we don't need to pass all these variables to the class itself
        command_string = f"""\"{self.OPENVPN_EXE_FULL_PATH}\" --config \"{self.OPENVPN_CONFIG_FOLDER_PATH}\\{conf_file}\" {self.additional_args_string} --verb 4"""

        sess = TB_SESSION(conf_file,
                          self.SESSION_CLASS_INITIALIZATION_TIMEOUT,
                          self.INTERNAL_REQUEST_TIMEOUT,
                          self.SESSION_CREATE_FUNCTION(), # default is requests.session()
                          command_string,
                          self.LOG_DIR,
                          self.vpn_tracker,
                          )
        self.sessions[conf_file] = sess
        return sess

    def list_online_sessions(self):  # blah
        for sess in self.sessions:
            if self.sessions[sess].state == SESSION_STATE.ONLINE:
                yield self.sessions[sess]

    def maintain_sessions(
            self) -> bool:  # main function, loops through sessions, recycles dead ones and spawns new ones.
        # returns true when all sessions are online
        for i in range(len(self.sessions),
                       self.NUM_CONCURRENT_VPN_PROCS):  # start new sessions if there are less than the limit
            if self.proc_create_interval_timer.ready():  # check process creation interval timer
                self.proc_create_interval_timer.delay()  # if ready, reset timer and ..
                self.make_new_session()  # fire up a new session
        kill_list = []  # ... list of sessions to kill
        any_offline = not len(
            self.sessions) == self.NUM_CONCURRENT_VPN_PROCS  # set return value to false if max sessions arn't initialized yet
        for sess in self.sessions:
            if not self.sessions[
                       sess].state == SESSION_STATE.ONLINE:  # if any session isn't online, set return value to false
                any_offline = True
            self.sessions[sess].do_stuff()  # run session task

            if self.sessions[
                sess].state == SESSION_STATE.CONNECTION_TESTED:  # if the state is CONNECTION_TESTED, both apis returned with valid info,
                # but we need to check that the traffic is flowing through a new remote ip
                if self.sessions[sess].remote_ip in self.ips:  # check if the remote ip is unique
                    self.sessions[sess].update_state(SESSION_STATE.TRAFFIC_MISMATCH)  # update state if it is not.
                else:
                    self.ips.append(self.sessions[sess].remote_ip)  # add ip to our list if it is.

            if self.sessions[sess].state < -1:  # sessions with state <-1 are dead
                kill_list.append(sess)  # add it to the kill list

            for sess_string_dic in self.sessions[sess].get_new_status_dics():  # print status strings
                p.print("session_strings", sess_string_dic)

        for sess in kill_list:  # remove dead sessions
            self.kill_session(sess)
        return not any_offline

    def default_get_next_conf(self):  # something better later
        conf = self.dir_list.pop()
        return conf
