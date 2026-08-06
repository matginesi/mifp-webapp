from mifp_app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=app.config.get("FLASK_HOST", "127.0.0.1"),
        port=int(app.config.get("FLASK_PORT", 8000)),
        debug=bool(app.config.get("FLASK_DEBUG", False)),
        threaded=True,
        use_reloader=False,
    )
