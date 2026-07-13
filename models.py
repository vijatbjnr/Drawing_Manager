import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Section(db.Model):
    __tablename__ = 'sections'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    color_theme = db.Column(db.String(50), nullable=False, default='blue')  # For colorful visual themes
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    folders = db.relationship('Folder', backref='section', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Section {self.name}>"


class Folder(db.Model):
    __tablename__ = 'folders'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('folders.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    subfolders = db.relationship('Folder', backref=db.backref('parent', remote_side=[id]), lazy=True, cascade="all, delete-orphan")
    drawings = db.relationship('Drawing', backref='folder', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        parent_str = f" under Folder ID {self.parent_id}" if self.parent_id else ""
        return f"<Folder {self.name} under Section ID {self.section_id}{parent_str}>"


class Drawing(db.Model):
    __tablename__ = 'drawings'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    drawing_number = db.Column(db.String(500), nullable=False, unique=True, index=True)
    revision = db.Column(db.String(20), nullable=False, default='00')
    description = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=True)  # File name or metadata path reference
    file_data = db.Column(db.LargeBinary, nullable=True)  # Raw binary bytes of PDF/Image/Excel file
    folder_id = db.Column(db.Integer, db.ForeignKey('folders.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<Drawing {self.drawing_number} - {self.title}>"


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"
