import os
from dotenv import load_dotenv
from tvDatafeed import TvDatafeed, Interval

load_dotenv()

TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")

def create_tv_connection():
    if TV_USERNAME and TV_PASSWORD:
        print(f"{TV_USERNAME}")
        print(f"{TV_PASSWORD}")
        return TvDatafeed(TV_USERNAME, TV_PASSWORD)

    return TvDatafeed()

tv = create_tv_connection()
