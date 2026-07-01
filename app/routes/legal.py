from flask import Blueprint, render_template

bp = Blueprint("legal", __name__)


@bp.get("/impressum")
def impressum():
    return render_template("impressum.html")


@bp.get("/datenschutz")
def datenschutz():
    return render_template("datenschutz.html")
