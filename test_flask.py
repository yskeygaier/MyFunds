from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

if __name__ == '__main__':
    print('Flask app initialized successfully')
    # 不实际启动服务器，只检查初始化是否成功
