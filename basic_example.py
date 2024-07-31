# Justin Brown - 7/31/2024 - Traffic Blaster
# several connections, synchronous requests


from src import TrafficBlaster
import requests


def my_receive_function(sess):
    return sess.response_list


def my_send_function(sess, urls):
    sess.response_list.clear()
    for url in urls:
        try:
            resp = sess.requests_session.get(url)
            sess.response_list.append(resp)
        except requests.exceptions.ConnectionError:
            pass


if __name__ == "__main__":

    test_urls = [
        'http://bbc.co.uk', 'http://www.cia.gov', 'http://www.msn.com'
    ]
    additional_ovpn_arguments = {
        "disable-dco": None
    }
    tb = TrafficBlaster(OPENVPN_EXE_FULL_PATH="C:\\Program Files\\OpenVPN\\bin\\openvpn.exe",
                        OPENVPN_CREDENTIALS_PATH="C:\\Users\\justi\\Documents\\ovpncreds.txt",
                        NUM_CONCURRENT_VPN_PROCS=5,
                        OPENVPN_ARGS=additional_ovpn_arguments
                        )
    waiting_to_send = True
    sent = False
    while not sent:
        if tb.maintain_sessions():
            print("sending...")
            for session in tb.list_online_sessions():
                my_send_function(session, test_urls)
            print("recieving...")
            for session in tb.list_online_sessions():
                responses = my_receive_function(session)
                for response in responses:
                    print(response)
            sent = True
