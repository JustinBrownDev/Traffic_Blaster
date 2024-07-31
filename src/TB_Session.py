# Justin Brown - 7/8/2024 - Traffic Blaster
import subprocess               # subprocess to run openvpn instance
from threading import Thread    # threads for non-blocking read from openvpn process's output
from queue import Queue, Empty  # queue for the same as above
from src.helpers import *
class TB_SESSION:
    def __init__(self, *args):
        self.DEBUG_OVPN_PROC_OUTPUT = False # Not using this anymore, leaving the variable here so it will stay that way

        # unpack arguments
        (self.conf_file,  # ...
         self.INITIALIZATION_TIMEOUT,  # Timeout for class to go online
         self.internal_request_timeout,  # Timeout for internally used requests (ip & geo)
         self.requests_session,
         self.command_string,
         self.log_dir,
         self.logger,
         ) = args

        self.status_lists = []                          # data that gets printed to the console
        self.ovpn_client_process = None                 # subprocess that runs the openvpn client
        self.time_initialized = datetime.datetime.now() # ...
        self.time_online = None                         # ... these are logged
        self.local_ip = None                            # local address the session binds to, retrieve this from ovpn output in read_pipe
        self.remote_ip = None                           # remote address where our vpn is located - set via check_connection
        self.geolocation = None                         # vpn location - set via check_connection
        self.q = None                                   # queue where the ovpn output is buffered
        self.state = None                               # state is the enum that determines which actions are taken in the do_stuff function
        self.update_state(SESSION_STATE.CLASS_INITIALIZED)
        self.last_state = self.state                    # used to send status data back when the state changes

        self.response_list = []                         # both of these are entirely for the user to use
        self.request_list = []                          #
        # Delays
        SESSION_INITIALIZATION_DELAYS = {               # seconds to wait before each task (seconds to wait after state has changed)
            SESSION_STATE.CLASS_INITIALIZED: 0,
            SESSION_STATE.VPN_CONNECTED: 3,
            SESSION_STATE.SESSION_MOUNTED: 10,
            SESSION_STATE.CONNECTION_TESTED: 10,
            SESSION_STATE.ONLINE: 5
        }
        self.initialization_step_timer = delay_timer(delay_dictionary=SESSION_INITIALIZATION_DELAYS,
                                                     start_as_ready=True)
        # timeout to get online after initialization
        self.initialization_timeout = delay_timer(default_delay_seconds=self.INITIALIZATION_TIMEOUT)

        # interval to recheck connection with apis
        self.check_connection_api_interval = delay_timer(default_delay_seconds=60, start_as_ready=True)

        # timeout after a connection that goes down
        self.connection_down_timeout = delay_timer(default_delay_seconds=150)

    # User Code
    def open(self) -> None:  #open a process
        if self.ovpn_client_process is not None:  # just in case
            print(f"[{self.conf_file}] ovpn client is already open")
            return None

        # Spawn the OpenVPN subprocess

        self.ovpn_client_process = subprocess.Popen(
            self.command_string,
            stdout=subprocess.PIPE
        )
        # Outside code #1 (spread throughout multiple places)
        # [https://stackoverflow.com/questions/375427/a-non-blocking-read-on-a-subprocess-pipe-in-python] response by jfs edited by ankostis
        # Thread to continuously read from the subproccess and put outputs on the queue
        self.q = Queue()

        # Should keep track of this guy !!
        t = Thread(target=ADD_OVPN_OUTPUT_TO_QUEUE, args=(self.ovpn_client_process.stdout, self.q))
        t.daemon = True
        t.start()
        # End of outside code
        # all done, update state, set initialization timeout to start counting down
        self.update_state(SESSION_STATE.VPN_CONNECTING)
        self.initialization_timeout.delay()

    def do_stuff(self) -> None:  # main function, called every loop, executes tasks depending on the current state.
                                 # i really gotta think of a better name for this
        if self.state == SESSION_STATE.CLASS_INITIALIZED:
            # 1) open() - create OpenVPN process & output queue
            self.open()
        # check if the initialization timeout has lapsed, but make sure we havent gone online since then
        elif self.initialization_timeout.ready() and not (
                self.state == SESSION_STATE.ONLINE or self.state == SESSION_STATE.CONNECTION_DOWN):
            self.update_state(SESSION_STATE.TIMED_OUT) # set state to timed_out
            # update log
            self.logger.update(self.conf_file, time_started=self.time_initialized, time_online=self.time_online)
        # check if the session is ready to do the next task
        elif self.initialization_step_timer.ready():
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
                self.connection_down_timeout.clear()
            elif self.state == SESSION_STATE.ONLINE:  # 8) online, clear timeouts, check connection if interval has lapsed
                if self.time_online is None: # this means the session just went online for the first time, log the data here
                    self.time_online = datetime.datetime.now()
                    self.logger.update(self.conf_file, time_started=self.time_initialized, time_online=self.time_online, ip=self.remote_ip, geolocation=self.geolocation)
                self.initialization_timeout.clear()
                self.check_connection()
            elif self.state == SESSION_STATE.CONNECTION_DOWN:  # if connection goes offline, start a timeout, keep checking connection
                if self.connection_down_timeout.ready():
                    self.update_state(SESSION_STATE.CONNECTION_DOWN_TIMEOUT)
                else:
                    self.check_connection()

    def get_new_status_dics(self) -> list:  # called by main class to get progress info
        status_list = [s for s in self.status_lists]
        self.status_lists.clear()
        return status_list

    # helper
    def update_state(self, state) -> None:  # change state & add new status
        self.state = state
        self.append_to_status_list()

    def append_to_status_list(self) -> None:  # append status, list of strings to be beautified by the pp class
        self.status_lists.append({"name": self.conf_file, "state": SESSION_STATE.name(self.state), "remote": self.remote_ip,
                                  "local": self.local_ip, "geolocation": self.geolocation})

    # Check Session
    def check_connection(self) -> None:  # get apis to verify IP and Geolocation
        if self.state == SESSION_STATE.TESTING_CONNECTION:  # if on initialization, then don't care about check_connection_api_interval
            ip = self.request_ip_api()
            if not ip:  # assume no response is a connection error
                self.update_state(SESSION_STATE.CONNECTION_ERROR)
                return
            self.remote_ip = ip # set self.remote_ip

            geolocation = self.request_geo_api() # repeat with geo api
            if not geolocation:
                self.update_state(SESSION_STATE.CONNECTION_ERROR)
                return
            self.geolocation = geolocation

            self.update_state(SESSION_STATE.CONNECTION_TESTED) # if we got here then connection test was successful,
            self.check_connection_api_interval.delay() # set the delay for our next connection check

        elif self.check_connection_api_interval.ready():  # if routine connection check (already online), and delay is ready
            self.check_connection_api_interval.delay()  # reset delay
            ip = self.request_ip_api()
            if not ip:  # no response
                if self.state == SESSION_STATE.ONLINE:  # if we were online, now we're not
                    self.connection_down_timeout.delay()    # start timer for going offline
                    self.update_state(SESSION_STATE.CONNECTION_DOWN) # set state
            elif not self.remote_ip == ip:  # wrong ip received, traffic going through wrong endpoint
                self.update_state(SESSION_STATE.TRAFFIC_MISMATCH)
            else:  # all good. set state online if it's not already, and clear timeouts for being offline
                self.update_state(SESSION_STATE.ONLINE)
                self.connection_down_timeout.clear()

    def request_ip_api(self) -> str | None: # get ip from emapp api
        try:
            sesresponse_ip = self.requests_session.get('http://emapp.cc/get_my_ip',
                                                       timeout=self.internal_request_timeout)
        except requests.exceptions.ConnectTimeout:
            return None
        except requests.exceptions.ConnectionError:
            return None

        if sesresponse_ip.status_code != 200:  # This never happens, probably unnecessary
            print(f"ip api: {sesresponse_ip.status_code}")
        ip = sesresponse_ip.text
        ses_dic_ip = eval(ip)  # this is really stupid, it's literally arbitrary code execution lmao !! replace me with string operations
        if type(ses_dic_ip) == dict and "result_data" in ses_dic_ip:  # if response is good
            ip = ses_dic_ip["result_data"]
            return ip
        else:  # if response is bad
            return None

    def request_geo_api(self) -> str | None: # get geolocation data from ip-api.com
        try:
            sesresponse_geo = self.requests_session.get(f"http://ip-api.com/json/{self.remote_ip}",
                                                        timeout=self.internal_request_timeout)
        except requests.exceptions.ConnectTimeout:
            return None
        except requests.exceptions.ConnectionError:
            return None
        if sesresponse_geo.status_code != 200:
            print(f"geo api: {sesresponse_geo.status_code}")
        item_geo = sesresponse_geo.text
        if item_geo:
            ses_dic_geo = eval(item_geo)  # !! dumb! replace me with string operations
            if "city" in ses_dic_geo:
                return ses_dic_geo["city"] + ", " + ses_dic_geo["country"]
            else:
                print(ses_dic_geo)
                return None

    # Networking
    def mount_session(self) -> bool:
        # Outside code #2
        # [https://stackoverflow.com/questions/48996494/send-http-request-through-specific-network-interface] response by MarSoft
        # This binds the requests session to our vpn's specific local ip
        self.requests_session.get_adapter('http://').init_poolmanager(
            connections=requests.adapters.DEFAULT_POOLSIZE, # 10 by default.
            maxsize=requests.adapters.DEFAULT_POOLSIZE,
            source_address=(self.local_ip, 0),
        )
        self.requests_session.get_adapter('https://').init_poolmanager(
            connections=requests.adapters.DEFAULT_POOLSIZE,
            maxsize=requests.adapters.DEFAULT_POOLSIZE,
            source_address=(self.local_ip, 0),
        )
        self.update_state(SESSION_STATE.SESSION_MOUNTED)
        return True

    # Process Management
    # Outside code #1 (spread throughout multiple places)
    # [https://stackoverflow.com/questions/375427/a-non-blocking-read-on-a-subprocess-pipe-in-python] response by jfs edited by ankostis
    # it just reads from the queue and decodes the output from bytes to string
    def get_line_from_queue(self) -> str | None:  # return a line if there's a line or None if there is no line
        line = None
        try:
            line = self.q.get_nowait().decode()  # or q.get(timeout=.1)
        except Empty:
            pass
        return line

    def read_pipe(self) -> None:
        line = self.get_line_from_queue()

        while line is not None:
            if self.DEBUG_OVPN_PROC_OUTPUT: # write line to log, last char is a newline so omit that
                with open(f"{self.log_dir}/ovpn_proc_output/{self.conf_file}.txt",'a') as file:
                    file.write(line[:-1])
            # the timestamp is not always the same length, hacky solution to accommodate that
            # this is ugly but functional
            line_without_datestr = line[line.find(' ', line.find(' ', line.find(' ') + 1) + 1) + 1:-1]  # do this better!!
            # look for "Initialization Sequence Completed" and not "Initialization Sequence Completed with Errors"
            # maybe line_without_datestr[:34] == "Initialization Sequence Completed\n" ?
            if line_without_datestr[:33] == "Initialization Sequence Completed" and len(line_without_datestr) <= 35: # if this is true, the vpn is online
                if self.local_ip is None:   # if vpn went online and we missed the ip.
                    self.update_state(SESSION_STATE.FAILED_TO_GET_VPN_ADDRESS)
                else: # vpn is ready
                    self.update_state(SESSION_STATE.VPN_CONNECTED)
            # this is where we get the local ip
            elif line_without_datestr[:55] == 'Notified TAP-Windows driver to set a DHCP IP/netmask of':
                start = 56 # start of ip
                i = start
                for c in line_without_datestr[i:]:  # for char in chars that come after the aforementioned string
                    if c.isnumeric() or c == '.':  # if the char is a number or a period we're still reading the ip, increment i
                        i += 1
                    else:  # if it's not we've hit the end of the ip
                        break
                # slice out the ip
                self.local_ip = line_without_datestr[start:i]
            # get the next line in output
            line = self.get_line_from_queue()

    def terminate(self) -> None:
        if self.requests_session:  # we may have not gotten that far
            self.requests_session.close()  # close the session
        if self.ovpn_client_process: # i find it hard to believe this would ever be false, but it cant hurt to check
            self.ovpn_client_process.terminate()  # terminate the process