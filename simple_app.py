from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

@app.route('/api/test')
def test():
    return {'success': True, 'message': 'Test API works!'}

if __name__ == '__main__':
    print('Starting simple Flask application...')
    app.run(debug=True, host='0.0.0.0', port=5001)
