from bs4 import BeautifulSoup
from flask import Flask, render_template, session, request, jsonify, redirect, url_for
import config
from models import db, User, LinkGPT, Conversation
import jwt
import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = config.SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = config.SQLALCHEMY_TRACK_MODIFICATIONS
app.secret_key = config.SECRET_KEY
SECRET_KEY = config.SECRET_KEY

db.init_app(app)

with app.app_context():
    db.create_all()


def decode_token(token):
    try:
        decoded_data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return decoded_data.get("username")  # Mengembalikan data yang sudah didekode
    except jwt.ExpiredSignatureError:
        return None  # Token kadaluarsa
    except jwt.InvalidTokenError:
        return None  # Token tidak valid
    
def scrape_link(link, timeout=100):
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')

    driver = webdriver.Chrome(options=options)

    driver.get(link)
    try: # async wait
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-message-author-role="user"]'))
        )
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div[data-message-author-role="assistant"]'))
        )
    except:
        driver.quit()
        return None, [], [], [], [] 
    
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')

    title = soup.find("title").text
    title = title.replace("ChatGPT - ", "")

    if not title:
        driver.quit()
        return None, [], [], [], []

    # take all prompt and response
    prompt_elements = soup.find_all('div', attrs={'data-message-author-role': 'user'})
    response_elements = soup.find_all('div', attrs={'data-message-author-role': 'assistant'})

    if prompt_elements == [] or response_elements == []:
        driver.quit()
        return None, [], [], [], []

    # close browser
    driver.quit()

    prompts = [chat.get_text() for chat in prompt_elements]
    responses = [chat.decode_contents() for chat in response_elements]
    pp_prompts = [chat.get_text().lower() for chat in prompt_elements]
    pp_responses = [chat.get_text().lower() for chat in response_elements]
    
    return title, prompts, responses, pp_prompts, pp_responses

@app.route("/")
def home():
    token = session.get("token")
    username = decode_token(token) if token else None
    name = None

    if token and not username:
        session.pop("token", None)

    links = []
    if username:
        user = User.query.filter_by(username=username).first()
        if not user:
            username = None
            session.pop("token", None)
        if user:
            name = user.name
            from sqlalchemy import desc
            links = LinkGPT.query.filter_by(id_user=user.id).order_by(desc(LinkGPT.updated_at)).all()


    data = [
        {
            "id": link.id,
            "user": 
            {
                "name": link.user.name,
                "username": link.user.username
            },
            "title": link.title,
            "link": link.link,
            "updated_at": link.updated_at,
            "conversations": [{"prompt": c.prompt, "response": c.response, "pp_prompt": c.pp_prompt, "pp_response": c.pp_response} for c in link.conversations]
        }
        for link in links
    ]


    return render_template("index.html", username=username, name=name, links=data)

@app.route("/register", methods=["POST"])
def register():
    username = request.form["username"]
    name = request.form["name"]
    password = request.form["password"]

    try:
        user = User(name=name, username=username)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        return jsonify({"success": True, "message": "User registered successfully"})
    except:
        return jsonify({"success": False, "message": "User registration failed"}), 400
    
@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    password = request.form["password"]

    user = User.query.filter_by(username=username).first()

    if not user or not user.check_password(password):
        return jsonify({"success": False, "message": "Invalid username or password"}), 401
    
    # create token jwt
    payload = { 
        "username": username, 
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1) # create token that expires in 1 hour
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    session["token"] = token

    return jsonify({"success": True, "message": "User logged in successfully"})

@app.route("/logout")
def logout():
    session.pop("token", None)
    return redirect(url_for("home"))

@app.route("/check-username", methods=["POST"])
def check_username():
    data = request.get_json()
    username = data.get("username", "")

    user = User.query.filter_by(username=username).first()
    if user:
        return jsonify({ "valid": False })
    
    return jsonify({ "valid": True })

@app.route("/check-link", methods=["POST"])
def check_link():
    data = request.get_json()
    link = data.get("link", "")

    link_gpt = LinkGPT.query.filter_by(link=link).first()
    if link_gpt:
        return jsonify({ "valid": False })

    return jsonify({ "valid": True })

@app.route("/upload-link", methods=["POST"])
def upload_link():
    token = session.get("token")
    username = decode_token(token) if token else None

    if not username:
        return jsonify({"success": False, "message": "User not logged in"}), 401
    
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    

    link = request.form["link"]

    title, prompts, responses, pp_prompts, pp_responses = scrape_link(link)

    if not title:
        return jsonify({"success": False, "message": "Link not found"}), 404
    
    if len(prompts) != len(responses):
        return jsonify({"success": False, "message": "Length missmatch"}), 400
    
    new_link = LinkGPT(id_user= user.id, title=title, link=link)
    db.session.add(new_link)
    db.session.commit()
    
    for i in range(len(prompts)):
        new_conv = Conversation(id_link=new_link.id, prompt=prompts[i], response=responses[i], pp_prompt=pp_prompts[i], pp_response=pp_responses[i])
        db.session.add(new_conv)

    db.session.commit()

    return jsonify({"success": True, "message": "Link uploaded successfully"})

@app.route("/edit-link", methods=["POST"])
def edit_link():
    token = session.get("token")
    username = decode_token(token) if token else None

    if not username:
        return jsonify({"success": False, "message": "User not logged in"}), 401
    
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    
    id_link = request.form["id-link"]
    link = request.form["link"]

    title, prompts, responses, pp_prompts, pp_responses = scrape_link(link)

    if not title:
        return jsonify({"success": False, "message": "Link not found"}), 404
    
    if len(prompts) != len(responses):
        return jsonify({"success": False, "message": "Length missmatch"}), 400
    
    link_gpt = LinkGPT.query.filter_by(id=id_link).first()
    if not link_gpt:
        return jsonify({"success": False, "message": "Link not found"}), 404
    
    db.session.query(Conversation).filter(Conversation.id_link == id_link).delete(synchronize_session=False)
    db.session.commit()

    link_gpt.title = title
    link_gpt.link = link
    db.session.commit()

    for i in range(len(prompts)):
        new_conv = Conversation(id_link=id_link, prompt=prompts[i], response=responses[i], pp_prompt=pp_prompts[i], pp_response=pp_responses[i])
        db.session.add(new_conv)

    db.session.commit()

    return jsonify({"success": True, "message": "Link edited successfully"})

@app.route("/delete-link", methods=["POST"])
def delete_link():
    token = session.get("token")
    username = decode_token(token) if token else None

    if not username:
        return jsonify({"success": False, "message": "User not logged in"}), 401
    
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    
    id_link = request.form["id-link"]

    link_gpt = LinkGPT.query.filter_by(id=id_link).first()
    if not link_gpt:
        return jsonify({"success": False, "message": "Link not found"}), 404
    
    db.session.query(Conversation).filter(Conversation.id_link == id_link).delete(synchronize_session=False)
    db.session.commit()

    db.session.delete(link_gpt)
    db.session.commit()

    return jsonify({"success": True, "message": "Link deleted successfully"})

if __name__ == "__main__":
    app.run(debug=True)