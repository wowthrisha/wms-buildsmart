from app import create_app
app = create_app()

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1'], port=port)
