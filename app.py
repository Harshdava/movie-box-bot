from flask import Flask
from threading import Thread
from waitress import serve  # <--- આ નવી લાઈન છે

app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Hello, Bot is running!'

def run():
    # અહીં આપણે app.run ને બદલે serve વાપરીશું
    serve(app, host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
