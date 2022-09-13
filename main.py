import os
import logging
from typing import Union, Dict
from fastapi import FastAPI, Form
from dotenv import load_dotenv
from pydantic import BaseModel
import requests
from requests.auth import HTTPBasicAuth
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from datetime import date, datetime

from menus import MENU



logging.basicConfig(level=logging.INFO)

# Get config vars. If not in heroku, load the dotenv file
is_prod = os.environ.get('IS_HEROKU', None)
if not is_prod:
    load_dotenv()

transport_api_id = os.environ.get('TRANSPORT_API_ID')
transport_api_key = os.environ.get('TRANSPORT_API_KEY')

twilio_api_id = os.environ.get('TWILIO_API_ID')
twilio_api_key = os.environ.get('TWILIO_API_KEY')

rttp_id = os.environ.get('ATTPID')
rttp_pw = os.environ.get('ATTPPW')

client = Client(twilio_api_id, twilio_api_key)


class TrainRequest(BaseModel):
    message: str


app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None):
    return {"item_id": item_id, "q": q}

def clean_departures(departure):
    tt_time = departure['aimed_departure_time']
    e_time = departure['expected_departure_time']
    pl = departure['platform']
    dest = departure['destination_name']
    f_time = tt_time if tt_time == e_time else f'{tt_time} ({e_time})'
    return {
        'time': f_time,
        'dest': dest,
        'platform': pl
    }

def format_departure(departure):
    return f"{departure['time']}, platform {departure['platform']} ({departure['dest']})"

def next_departures(from_station: str, to_station: str, n: int=2):
    response = requests.get(
        f"https://transportapi.com/v3/uk/train/station/{from_station}/live.json?app_id={transport_api_id}&app_key={transport_api_key}&calling_at={to_station}&darwin=false&train_status=passenger"
    )
    output_dict = response.json()
    departures = output_dict['departures']['all']
    logging.debug(n)
    if len(departures) == 0:
        return "None found"
    n_fetch = min([n, len(departures)])
    dep = [clean_departures(d) for d in departures[:n_fetch]]
    out_string = f"Next from {from_station} -> {to_station}\n"
    out_string += '\n'.join([format_departure(d) for d in dep])
    return out_string

class Station:
    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon
    def __str__(self):
        return f"lonlat:{self.lon},{self.lat}"

stations = {}

def get_station_info(station_code):
    station_code = station_code.upper()
    if not station_code in stations:
        logging.info("Quering for location of %s", station_code)
        request = requests.get(
            f"https://transportapi.com/v3/uk/places.json?app_id={transport_api_id}&app_key={transport_api_key}&query={station_code}&type=train_station"
        )
        try:
            logging.info(request.json())
            station_info = list(filter(lambda x: x['station_code'] == station_code, request.json()['member']))[0]
            stations[station_code] = Station(station_info['latitude'], station_info['longitude'])
        except Exception:
            return None
    else:
        logging.info("Extracting info for %s from cache", station_code)
    return stations[station_code]

class Leg:
    def __init__(self, train_part: Dict):
        self.from_name = train_part['from_point_name']
        self.to_name = train_part['to_point_name']
        self.departure_time = train_part['departure_time']
        self.arrival_time = train_part['arrival_time']
        self.destination = train_part['destination']

    def __str__(self):
        return f"{self.from_name} {self.departure_time} -> {self.arrival_time} {self.to_name} (train to {self.destination})"

def clean_route(route: Dict) -> str:
    train_parts = [Leg(r) for r in route['route_parts'] if r['mode'] == 'train']
    return_str = "\n".join(str(t) for t in train_parts)
    return return_str
        
    

@app.get("/planner/")
def planner(from_station: str, to_station: str, n_fetch: int=2):
    from_info = get_station_info(from_station)
    to_info = get_station_info(to_station)
    if from_info is None or to_info is None:
        return "Error accessing station info"

    request_url = f"https://transportapi.com/v3/uk/public/journey/from/{str(from_info)}/to/{str(to_info)}.json?app_id={transport_api_id}&app_key={transport_api_key}&modes=train&service=silverrail"
    logging.info(request_url)
    try:
        routes = requests.get(request_url).json()['routes']
    except:
        return "Error performing get"
    logging.info("Found %d routes", len(routes))
    routes = routes[:min(n_fetch, len(routes))]
    cleaned_routes = [clean_route(r) for r in routes]
    ret = "\n\n".join([f"{i + 1}: {s}" for i, s in enumerate(cleaned_routes)])
    logging.info(ret)
    return ret


@app.post("/train_request")
def train_request(request_info: TrainRequest):
    if request_info.message.lower().startswith("next train home"):
        tokens = request_info.message.split()
        source = tokens[3].upper()
        if len(tokens) > 4:
            n = int(tokens[4])
        else:
            n = 2
        logging.debug("Next train home from %s", source)
        return next_departures(source, "MIN", n=n)
    if request_info.message.lower().startswith("next train gla"):
        logging.debug("Next train to gla")
        return next_departures("MIN", "GLC")
    if request_info.message.lower().startswith("next arrival"):
        to_station = request_info.message.split()[2].upper()
        from_station = request_info.message.split()[3].upper()
        return next_arrival(to_station, from_station)
    if request_info.message.lower().startswith("planner"):
        tokens = request_info.message.lower().split()
        from_station = tokens[1]
        to_station = tokens[2]
        if len(tokens) > 3:
            n_fetch = int(tokens[3])
        else:
            n_fetch = 2
        return next_trains(from_station, to_station, n_fetch)

class Service:
    def __init__(self, d_time, a_time, platform, plat_conf):
        self.d_time = d_time
        self.a_time = a_time
        self.platform = platform
        self.plat_conf = plat_conf

    def __str__(self):
        return f"{self.d_time} -> {self.a_time}, platform {self.platform} ({self.plat_conf})"

def process_rttp_services(response: Dict, dest: str, n: int) -> str:
    if response['services']:
        n_services = len(response['services'])
        n = min(n, n_services)
        neat_services = []
        for i in range(n):
            this_service = response['services'][i]
            service_id = this_service['serviceUid']
            times = service_arrival(service_id, dest.upper())
            neat_services.append(
                Service(
                    this_service['locationDetail']['origin'][0]['publicTime'],
                    times['arrive'],
                    this_service['locationDetail']['platform'],
                    'confirmed' if this_service['locationDetail']['platformConfirmed'] else "uncomfirmed"
                ) 
            )   
        return neat_services
    else:
        return ["No services found"]

@app.get("/next_trains")
def next_trains(from_station: str, to_station: str, n: int):
    auth = HTTPBasicAuth(rttp_id, rttp_pw)
    request_url = f"https://api.rtt.io/api/v1/json/search/{from_station}/to/{to_station}"
    response = requests.get(request_url, auth=auth)
    print(response.status_code)
    if response.status_code == 200:
        response = response.json()
        if "error" in response:
            return response['error']
        else:
            neat_services = process_rttp_services(response, to_station, n)
            return_str = "\n".join([str(s) for s in neat_services])
    else:
        return_str = "Error getting response. Check station codes"
    return return_str

@app.get("/service_arrival")
def service_arrival(service_id: str, calling_point: str):
    auth = HTTPBasicAuth(rttp_id, rttp_pw)
    year = datetime.now().year
    month = datetime.now().month
    day = datetime.now().day
    request_url = f"https://api.rtt.io/api/v1/json/service/{service_id}/{year}/{month:02}/{day:02}"
    response = requests.get(request_url, auth=auth)
    locations = response.json()['locations']
    locations = list(filter(lambda x: x['crs'] == calling_point.upper(), locations))[0]
    departure_time = locations['origin'][0]['publicTime']
    arrival_time = locations['realtimeArrival']
    return {'depart': departure_time, 'arrive': arrival_time}

@app.get("/menu")
def menu():
    today = date.today()
    return_dict = {'date': today}
    start_date = date(2022, 8, 15)
    day_diff = (today - start_date).days
    week_diff = day_diff // 7
    if week_diff % 2 == 0:
        week = 1
    else:
        week = 2
    return_dict['week'] = week
    day_of_week = today.isoweekday()
    return_dict['day_of_week'] = day_of_week
    return_dict['menu'] = MENU[week][day_of_week]
    
    return return_dict

def next_arrival(to_station: str, from_station: str):
    logging.info("Next arrival at %s from %s", to_station, from_station)
    response = requests.get(
        f"https://transportapi.com/v3/uk/train/station/{to_station}/live.json?app_id={transport_api_id}&app_key={transport_api_key}&called_at={from_station}&darwin=false&train_status=passenger&type=arrival"
    )
    logging.info(response.json())
    output_list = response.json()['arrivals']['all']
    logging.info("returned %d", len(output_list))
    if len(output_list) == 0:
        # try "calling_at" instead
        response = requests.get(
            f"https://transportapi.com/v3/uk/train/station/{to_station}/live.json?app_id={transport_api_id}&app_key={transport_api_key}&calling_at={from_station}&darwin=false&train_status=passenger&type=arrival"
        )
        output_list = response.json()['arrivals']['all']
        logging.info("returned %d", len(output_list))
        if len(output_list) == 0:
            return "Nothing found"
    else:
        output = output_list[0]
        return_string = f"{to_station}: {output['aimed_arrival_time']} ({output['status']}). Going to {output['destination_name']}."
        if not output['status'] == 'ON TIME':
            return_string += f" Expected: {output['expected_arrival_time']}"
        return return_string
    
@app.post("/twilio_message")
def twilio_message(From: str = Form(...), Body: str = Form(...)):
    tr = TrainRequest(message=Body)
    message = train_request(tr)
    logging.info(message)
    if message is None or len(message) == 0:
        message = "Nothing found"
    message = client.messages.create(
        body=message,
        to=From,
        from_="+447360279176"
    )
    return str(message)
    
@app.get("/test")
def test():
    logging.info(client)
    message = client.messages.create(
        body="Test message sending",
        to="+447900055707",
        from_="+447360279176"
    )
    return str(message)
