from typing import Union
from deta import Deta
from fastapi import FastAPI
import requests

app = FastAPI()


deta = Deta("a0bjwp2i_5Qn5tqpunHSynHXjgzvaA5roGkSobMra")
db = deta.Base("test-db")  


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

def next_departures(from_station: str, to_station: str, n: int=2):
    response = requests.get(
        f"https://transportapi.com/v3/uk/train/station/{from_station}/live.json?app_id=7166fe7b&app_key=d2f47147e4bf5234e6c4545907253a46&calling_at={to_station}&darwin=false&train_status=passenger"
    )
    output_dict = response.json()
    departures = output_dict['departures']['all']
    print(n)
    n_fetch = min([n, len(departures)])
    dep = [clean_departures(d) for d in departures[:n_fetch]]
    return dep


@app.get("/next_train_home")
def next_train_home(source: str="GLC"):
    return next_departures(source, "MIN")

@app.get("/next_train_gla")
def next_train_gla():
    return next_departures("MIN", "GLC")

@app.get("/store/")
def store(key: str, val:str):
    db.put({key: val})

@app.get("/retreive")
def retreive():
    return db.fetch({"name": "simon"})
