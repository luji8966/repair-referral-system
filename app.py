from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime
import random
import string


app = Flask(__name__)


# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///referral.db"

db = SQLAlchemy(app)
migrate = Migrate(app, db)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    openid = db.Column(db.String(255), unique=True, nullable=True)
    invite_code = db.Column(db.String(20), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0.0)


class Referral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reward = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


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


def add_referral(member, amount, reward_rate=0.05):
    reward = round(amount * reward_rate, 2)

    referral = Referral(
        member_id=member.id,
        amount=amount,
        reward=reward
    )

    member.balance += reward

    db.session.add(referral)
    db.session.commit()

    return referral


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register")
def register():
    return render_template("register.html")


@app.route("/staff", methods=["GET", "POST"])
def staff():
    if request.method == "POST":
        invite_code = request.form.get("invite_code")
        amount = float(request.form.get("amount"))

        member = Member.query.filter_by(invite_code=invite_code).first()

        if member is None:
            return "Member not found"

        referral = add_referral(member, amount)

        return (
            f"Referral added successfully! "
            f"Reward: £{referral.reward:.2f} | "
            f"New balance: £{member.balance:.2f}"
        )

    return render_template("staff.html")


if __name__ == "__main__":
    app.run(debug=True)