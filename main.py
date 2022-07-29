import os
import logging
from typing import Union
from fastapi import FastAPI, Form
from dotenv import load_dotenv
from pydantic import BaseModel
import requests
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

client = Client('AC84bbd218dc7d1e3d6d367b44752322fd', '9a633c8f5bff864b9609a6a7db499631')


logging.basicConfig(level=logging.INFO)

# Get config vars. If not in heroku, load the dotenv file
is_prod = os.environ.get('IS_HEROKU', None)
if not is_prod:
    load_dotenv()

transport_api_id = os.environ.get('TRANSPORT_API_ID')
transport_api_key = os.environ.get('TRANSPORT_API_KEY')


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


@app.post("/train_request")
def train_request(request_info: TrainRequest):
    if request_info.message.lower().startswith("next train home"):
        tokens = request_info.message.split()
        source = tokens[3].upper()
        if len(tokens) > 3:
            n = int(tokens[4])
        logging.debug("Next train home from %s", source)
        return next_departures(source, "MIN", n=n)
    if request_info.message.lower().startswith("next train gla"):
        logging.debug("Next train to gla")
        return next_departures("MIN", "GLC")
    if request_info.message.lower().startswith("next arrival"):
        to_station = request_info.message.split()[2].upper()
        from_station = request_info.message.split()[3].upper()
        return next_arrival(to_station, from_station)

def next_arrival(to_station: str, from_station: str):
    logging.info("Next arrival at %s from %s", to_station, from_station)
    response = requests.get(
        f"https://transportapi.com/v3/uk/train/station/{to_station}/live.json?app_id={transport_api_id}&app_key={transport_api_key}&called_at={from_station}&darwin=false&train_status=passenger&type=arrival"
    )
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
    message = train_request(tr)
    logging.info(message)
    message = client.messages.create(
        body=message,
        to="+447900055707",
        from_="+447360279176"
    )
    return str(message)
    