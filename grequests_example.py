# Justin Brown - 7/30/2024 - Traffic Blaster

# Single connection, async requests using grequests


import grequests
from src import TrafficBlaster


def my_async_receive_function(session):
    return grequests.imap(session.request_list, size=10)


def my_async_send_function(session, urls):
    session.request_list.clear()
    session.request_list.extend([grequests.get(url, session=session.requests_session) for url in urls])


if __name__ == "__main__":

    test_urls = [
        'http://bbc.co.uk', 'http://www.youtube.com', 'http://www.cia.gov', 'http://www.whitehouse.gov',
        'https://www.blogger.com', 'https://www.apple.com',
        'https://www.nih.gov', 'https://www.nytimes.com', 'https://www.twitter.com',
        'https://www.github.com', 'https://www.vimeo.com', 'https://www.cpanel.net',
        'https://www.draft.blogger.com', 'https://www.draft.blogger.com', 'https://www.www.weebly.com',
        'https://www.myspace.com', 'https://www.dan.com',
    ]
    additional_args = {'disable-dco':None}
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CONFIG_FOLDER_PATH="./ovpn configs/",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        NUM_CONCURRENT_VPN_PROCS=1,
                        OPENVPN_ARGS=additional_args
                        )
    sent = False
    while not sent:
        if tb.maintain_sessions():
            print("sending...")
            for session in tb.list_online_sessions():
                my_async_send_function(session, test_urls)
            print("recieving...")
            for session in tb.list_online_sessions():
                responses = my_async_receive_function(session)
                for response in responses:
                    print(response)
            sent = True
