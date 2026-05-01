from flask import Flask, send_from_directory
import os

app = Flask(__name__, static_folder=".")

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port)
