# Justin Brown - 7/8/2024 - Traffic Blaster
import grequests  # async reqs
from src import TrafficBlaster
#To Do
# Function Annotations
# Wrap make some functions to decrease the amount of config variables passed around as args

def my_recieve_function(session):
    l = session.response_list[:]
    session.response_list.clear()
    return l


def my_send_function(session, urls):
    session.request_list.clear()
    session.request_list.extend([grequests.get(url, session=session.requests_session) for url in urls])
    session.response_list.clear()
    session.response_list.extend(grequests.map(session.request_list, size=0))




if __name__ == "__main__":
    test_urls = [
        'http://bbc.co.uk', 'http://www.youtube.com', 'http://www.cia.gov', 'http://www.whitehouse.gov',
        'https://www.blogger.com', 'https://www.apple.com',
        'https://www.nih.gov', 'https://www.nytimes.com', 'https://www.twitter.com', 'https://www.twitter.com',
        'https://www.github.com', 'https://www.vimeo.com', 'https://www.cpanel.net',
        'https://www.draft.blogger.com', 'https://www.draft.blogger.com', 'https://www.www.weebly.com',
        'https://www.myspace.com', 'https://www.dan.com',
    ]
    additional_ovpn_arguments = {
        "disable-dco": None,
        "connect-timeout": 30,
        "server-poll-timeout": 30
    }
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CONFIG_FOLDER_PATH="/ovpn configs",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        NUM_CONCURRENT_VPN_PROCS=5,
                        PROC_CREATE_INTERVAL=5,
                        SESSION_CLASS_INITIALIZATION_TIMEOUT=60,
                        USER_DEFINED_SEND=my_send_function,
                        USER_DEFINED_RECEIVE=my_recieve_function,

                        )
    waiting_to_send = True
    while True:
        if tb.maintain_sessions() and waiting_to_send:
            waiting_to_send = False
            print("sending...")
            tb.add_urls_all_sessions(test_urls)
