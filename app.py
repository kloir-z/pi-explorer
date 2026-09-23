"""Entry point: `python app.py` serves Pi Explorer on port 5125."""
from explorer import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5125, debug=False)
