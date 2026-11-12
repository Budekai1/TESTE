import os
from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    resultado = ""
    if request.method == "POST":
        lote = request.form.get("lote", "").strip().upper()
        if lote:
            resultado = f"Lote {lote} validado pelo Python com sucesso!"
    return render_template("index.html", resultado=resultado)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
