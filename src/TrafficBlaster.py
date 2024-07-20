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


class TrafficBlaster:
    def __init__(self, **args):
        # Class Declarations ==========
        self.log = Logger()  # <--- Replace me with something better please

        #self.delay = delay_timer()
        # OPENVPN SETTINGS ============
        if "OVPN_COMMAND_FUNCTION" in args:
            self.OVPN_COMMAND_FUNCTION = args.get("OVPN_COMMAND_FUNCTION")
        elif not (args.get("OPENVPN_EXE_FULL_PATH") and args.get("OPENVPN_CONFIG_FOLDER_PATH")):
            print(
                "you must supply at least the path to an openvpn.exe file, and a path to a directory with .ovpn configuration files")
            print("ex:\n\tTrafficBlaster(OPENVPN_EXE_FULL_PATH = ..., OPENVPN_CONFIG_FOLDER_PATH = ...)")
        else:

            self.OPENVPN_EXE_FULL_PATH = args.get("OPENVPN_EXE_FULL_PATH")
            self.OPENVPN_CONFIG_FOLDER_PATH = args.get("OPENVPN_CONFIG_FOLDER_PATH")
            self.OPENVPN_CREDENTIALS_PATH = args.get("OPENVPN_CREDENTIALS_PATH")
            self.OPENVPN_ARGS = args.get("OPENVPN_ARGS", {})
            self.OPENVPN_ARGS["auth-user-pass"] = self.OPENVPN_CREDENTIALS_PATH

            additional_args = ""
            for arg in self.OPENVPN_ARGS:
                additional_args += f"--{arg} "
                value = self.OPENVPN_ARGS.get(arg)
                if value is not None:
                    additional_args += f"{value} "

            def GET_OVPN_COMMAND_STRING(file_name):
                return f"""\"{self.OPENVPN_EXE_FULL_PATH}\" --config \"{self.OPENVPN_CONFIG_FOLDER_PATH}\\{file_name}\" {additional_args}"""
            #DEFINE_GET_OVPN_COMMAND_STRING(self.OPENVPN_EXE_FULL_PATH, self.OPENVPN_CONFIG_FOLDER_PATH,
            #self.OPENVPN_ARGS)

        # Timeouts
        self.REQUEST_TIMEOUT = 30
        self.INTERNAL_REQUEST_TIMEOUT = 30
        self.SESSION_CLASS_INITIALIZATION_TIMEOUT = args.get("SESSION_CLASS_INITIALIZATION_TIMEOUT", 40)
        self.RECYCLE_SESSION_TIMEOUT_SECONDS = args.get("RECYCLE_SESSION_TIMEOUT_SECONDS", 60 * 5)
        # Delays
        # Traffic Blaster Settings
        self.WAIT_AFTER_VPN_CREATE_SECONDS = args.get("WAIT_AFTER_VPN_CREATE_SECONDS",
                                                      5)  # Potentially effective at preventing windows getting confused. Slows testing
        self.NUM_CONCURRENT_VPN_PROCS = args.get("NUM_CONCURRENT_VPN_PROCS",
                                                 7)  # Most stable at 1 core / connection. works fine over, kinda
        self.NUM_PROCS_RESERVED_FOR_SWAPPING = args.get("NUM_PROCS_RESERVED_FOR_SWAPPING",
                                                        0)  # Basically hardcoded right now. Would be nice to implement this functionality

        self.LIMIT_PER_LOCATION = args.get("LIMIT_PER_LOCATION", -1)  # This would be neat
        self.SESSION_CREATE_FUNCTION = args.get("SESSION_CREATE_FUNCTION", DEFAULT_CREATE_SESSION)
        # runtime variables
        self.dir_list = os.listdir(self.OPENVPN_CONFIG_FOLDER_PATH)
        random.shuffle(self.dir_list)
        self.sessions = {}
        self.PROC_CREATE_INTERVAL_SECONDS = args.get("PROC_CREATE_INTERVAL", 60)
        self.USER_DEFINED_SEND = args.get("USER_DEFINED_SEND", None)
        self.USER_DEFINED_RECEIVE = args.get("USER_DEFINED_RECEIVE", None)
        self.proc_create_delay_timer = delay_timer(default_delay_seconds=self.PROC_CREATE_INTERVAL_SECONDS,
                                                   start_as_ready=True)
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
            if self.sessions[sess].state == SESSION_STATE.ONLINE:
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
        any_offline = not len(self.sessions) == self.NUM_CONCURRENT_VPN_PROCS
        if len(self.sessions) > 0:
            for sess in self.sessions:
                urls = None
                if sess in self.urls_to_send:
                    urls = self.urls_to_send.pop(sess)
                resps = self.sessions[sess].USER_DEFINED_RECEIVE(self.sessions[sess])
                if resps:
                    for resp in resps:
                        if resp:
                            print(
                                f"[{self.sessions[sess].name}]({resp.status_code}) {resp.request.url} -> {resp.url}: {resp.text[:50]}")
                if self.sessions[sess].state == SESSION_STATE.ONLINE:
                    if urls:
                        self.sessions[sess].USER_DEFINED_SEND(self.sessions[sess], urls)
                else:
                    any_offline = True
                state = self.sessions[sess].do_stuff()
                if state == SESSION_STATE.CONNECTION_TESTED:
                    if self.sessions[sess].remote_ip in self.ips:
                        self.sessions[sess].update_state(SESSION_STATE.TRAFFIC_MISMATCH)
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
                          conf_file,
                          self.SESSION_CLASS_INITIALIZATION_TIMEOUT,
                          self.REQUEST_TIMEOUT,
                          self.INTERNAL_REQUEST_TIMEOUT,
                          self.USER_DEFINED_SEND,
                          self.USER_DEFINED_RECEIVE,
                          self.SESSION_CREATE_FUNCTION
                          )
        self.sessions[name] = sess
        return name
