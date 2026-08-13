from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
import random
import string


app = Flask(__name__)


# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///referral.db"

db = SQLAlchemy(app)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    openid = db.Column(db.String(255), unique=True, nullable=True)
    invite_code = db.Column(db.String(20), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0.0)


def generate_invite_code():
    letters = string.ascii_uppercase
    numbers = string.digits
    characters = letters + numbers

    while True:
        code = "".join(random.choices(characters, k=6))

        existing_member = Member.query.filter_by(invite_code=code).first()

        if existing_member is None:
            return code

        
def create_member(openid=None):
    if openid:
        existing_member = Member.query.filter_by(openid=openid).first()

        if existing_member:
            return existing_member

    invite_code = generate_invite_code()

    member = Member(
        openid=openid,
        invite_code=invite_code
    )

    db.session.add(member)
    db.session.commit()

    return member


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register")
def register():
    return render_template("register.html")


if __name__ == "__main__":
    app.run(debug=True)