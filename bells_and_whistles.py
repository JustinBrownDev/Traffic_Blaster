import requests
from src import TrafficBlaster
import os
from datetime import datetime, timedelta

dirlist = os.listdir("./ovpn configs/")
sorted_dirlist = sorted(dirlist, reverse=True)


def conf_select():
    return sorted_dirlist.pop()


class MySession(requests.Session):
    def get_with_timedelta(self, url):
        try:
            start = datetime.now()
            resp = self.get(url)
            return resp, datetime.now() - start
        except requests.exceptions.ConnectionError:
            pass



def get_instance_of_MySession():
    return MySession()



if __name__ == "__main__":

    test_urls = [
        'http://bbc.co.uk', 'http://www.cia.gov', 'http://www.msn.com'
    ]
    additional_args = {'disable-dco': None}
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CONFIG_FOLDER_PATH="./ovpn configs/",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        SESSION_CREATE_FUNCTION=get_instance_of_MySession,
                        CONFIG_SELECT_FUNCTION=conf_select,
                        NUM_CONCURRENT_VPN_PROCS=5,
                        OPENVPN_ARGS=additional_args
                        )
    waiting_to_send = True
    sent = False
    while not sent:
        if tb.maintain_sessions():
            print("sending...")
            for session in tb.list_online_sessions():
                for url in test_urls:
                    response, timedelta = session.requests_session.get_with_timedelta(url)
                    print(f"session:{session.conf_file} ({timedelta}):{response}")
            sent = True
