import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_file, abort
from models import db, Section, Folder, Drawing, User




# Load environment variables from .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_drawing_archive_1234')

# Database configuration
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is missing! "
        "A valid PostgreSQL connection string is required to start the application (e.g., postgresql://...). "
        "Please define it in your .env file or environment variables."
    )

# Support postgresql+pg8000 out of the box to avoid Windows C++ compiler wheel errors on Python 3.14
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+pg8000" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)

def ensure_database_exists(database_url):
    import urllib.parse
    import pg8000
    
    url_clean = database_url
    if url_clean.startswith("postgresql+pg8000://"):
        url_clean = url_clean.replace("postgresql+pg8000://", "postgresql://", 1)
    elif url_clean.startswith("postgres://"):
        url_clean = url_clean.replace("postgres://", "postgresql://", 1)
        
    try:
        parsed = urllib.parse.urlparse(url_clean)
        db_name = parsed.path.lstrip('/')
        if not db_name or db_name.lower() == 'postgres':
            return
            
        username = parsed.username
        password = urllib.parse.unquote(parsed.password) if parsed.password else None
        host = parsed.hostname or 'localhost'
        port = parsed.port or 5432
        
        # Connect to system 'postgres' database
        conn = pg8000.connect(
            user=username,
            password=password,
            host=host,
            port=port,
            database='postgres'
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
        exists = cursor.fetchone()
        
        if not exists:
            print(f"[INFO] Database '{db_name}' does not exist on PostgreSQL server. Creating it now...")
            cursor.execute(f'CREATE DATABASE "{db_name}"')
            print(f"[SUCCESS] Database '{db_name}' created successfully!")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[WARNING] Database auto-creation check failed for PostgreSQL: {e}")

# Ensure database exists before connecting SQLAlchemy
ensure_database_exists(DATABASE_URL)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db.init_app(app)

# Create tables if they do not exist
with app.app_context():
    db.create_all()
    # 1. Alter tables to add new columns if they do not exist
    try:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        
    try:
        db.session.execute(db.text("ALTER TABLE drawings ADD COLUMN file_data BYTEA;"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        
    # 2. Seed a default admin user if not exists
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user:
        admin_user = User(username='admin', is_admin=True)
        admin_user.set_password('admin123')
        db.session.add(admin_user)
        db.session.commit()
        print("[SUCCESS] Default admin user initialized (Username: admin, Password: admin123)")
    else:
        if not admin_user.is_admin:
            admin_user.is_admin = True
            db.session.commit()
            print("[INFO] Existing admin user promoted to Administrator role.")



# Context processor to inject global stats/sections to all templates
@app.context_processor
def inject_global_data():
    sections = Section.query.order_by(Section.name).all()
    drawing_count = Drawing.query.count()
    
    current_user = None
    if 'user_id' in session:
        current_user = db.session.get(User, session['user_id'])
        
    return {
        'global_sections': sections,
        'current_user': current_user,
        'global_stats': {
            'drawings': drawing_count
        }
    }

@app.before_request
def require_login():
    # Allow login, register, static assets, and pre-request assets
    allowed_routes = ['login', 'register', 'static']
    if request.endpoint not in allowed_routes and not request.path.startswith('/static/'):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        # Check if the user exists in the database (critical after migrations or DB resets)
        user = db.session.get(User, session['user_id'])
        if not user:
            session.pop('user_id', None)
            return redirect(url_for('login'))
            
        # Protect admin routes
        admin_routes = ['add_item', 'upload_drawing', 'edit_drawing', 'delete_drawing']
        if request.endpoint in admin_routes and not user.is_admin:
            flash("You do not have administrative permissions to perform this action.", "error")
            return redirect(url_for('dashboard'))

# Route: User Registration
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash("Username and password are required!", "error")
            return redirect(url_for('register'))
            
        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Username already exists! Choose another one.", "error")
            return redirect(url_for('register'))
            
        new_user = User(username=username)
        new_user.set_password(password)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating account: {str(e)}", "error")
            return redirect(url_for('register'))
            
    return render_template('register.html')

# Route: User Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash("Please enter both username and password.", "error")
            return redirect(url_for('login'))
            
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return redirect(url_for('login'))
            
        session['user_id'] = user.id
        flash(f"Welcome back, {user.username}!", "success")
        return redirect(url_for('dashboard'))
        
    return render_template('login.html')

# Route: User Logout
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))

# Route: Dashboard
@app.route('/')
def dashboard():
    sections = Section.query.order_by(Section.name).all()
    recent_drawings = Drawing.query.order_by(Drawing.created_at.desc()).limit(6).all()
    
    # Calculate stats for dashboard
    section_count = Section.query.count()
    folder_count = Folder.query.count()
    drawing_count = Drawing.query.count()
    
    # Perform optimized grouped count queries to avoid N+1 database queries
    folder_counts = db.session.query(Folder.section_id, db.func.count(Folder.id)).group_by(Folder.section_id).all()
    folder_count_map = {sec_id: count for sec_id, count in folder_counts}
    
    drawing_counts = db.session.query(Folder.section_id, db.func.count(Drawing.id)).join(
        Drawing, Folder.id == Drawing.folder_id
    ).group_by(Folder.section_id).all()
    drawing_count_map = {sec_id: count for sec_id, count in drawing_counts}
    
    stats_per_section = []
    for sec in sections:
        stats_per_section.append({
            'section': sec,
            'folder_count': folder_count_map.get(sec.id, 0),
            'drawing_count': drawing_count_map.get(sec.id, 0)
        })

    return render_template('dashboard.html', 
                           sections_data=stats_per_section, 
                           recent_drawings=recent_drawings,
                           global_stats={
                               'sections': section_count,
                               'folders': folder_count,
                               'drawings': drawing_count
                           })

# Route: List All Sections
@app.route('/sections')
def list_sections():
    sections = Section.query.order_by(Section.name).all()
    
    folder_counts = db.session.query(Folder.section_id, db.func.count(Folder.id)).group_by(Folder.section_id).all()
    folder_count_map = {sec_id: count for sec_id, count in folder_counts}
    
    drawing_counts = db.session.query(Folder.section_id, db.func.count(Drawing.id)).join(
        Drawing, Folder.id == Drawing.folder_id
    ).group_by(Folder.section_id).all()
    drawing_count_map = {sec_id: count for sec_id, count in drawing_counts}
    
    stats_per_section = []
    for sec in sections:
        stats_per_section.append({
            'section': sec,
            'folder_count': folder_count_map.get(sec.id, 0),
            'drawing_count': drawing_count_map.get(sec.id, 0)
        })
    return render_template('sections.html', sections_data=stats_per_section)

# Route: Section Details (List Folders)
@app.route('/section/<int:section_id>')
def section_detail(section_id):
    section = Section.query.get_or_404(section_id)
    # Only show top-level folders (parent_id is None)
    folders = Folder.query.filter_by(section_id=section_id, parent_id=None).order_by(Folder.name).all()
    
    # Get drawing counts for folders
    folder_data = []
    for folder in folders:
        d_count = Drawing.query.filter_by(folder_id=folder.id).count()
        folder_data.append({
            'folder': folder,
            'drawing_count': d_count
        })
        
    return render_template('section_detail.html', section=section, folders=folder_data)

# Route: Folder Details (List Drawings and Subfolders)
@app.route('/folder/<int:folder_id>')
def folder_detail(folder_id):
    folder = Folder.query.get_or_404(folder_id)
    section = Section.query.get(folder.section_id)
    
    # Get subfolders of this folder
    subfolders_list = Folder.query.filter_by(parent_id=folder_id).order_by(Folder.name).all()
    subfolder_data = []
    for f in subfolders_list:
        d_count = Drawing.query.filter_by(folder_id=f.id).count()
        subfolder_data.append({
            'folder': f,
            'drawing_count': d_count
        })
    
    # Handle search/filter inside the folder
    search_query = request.args.get('q', '').strip()
    if search_query:
        drawings = Drawing.query.filter(
            Drawing.folder_id == folder_id,
            (Drawing.title.ilike(f'%{search_query}%')) | 
            (Drawing.drawing_number.ilike(f'%{search_query}%')) |
            (Drawing.description.ilike(f'%{search_query}%'))
        ).order_by(Drawing.title).all()
    else:
        drawings = Drawing.query.filter_by(folder_id=folder_id).order_by(Drawing.title).all()
        
    # Build recursive parent trail list for navigation breadcrumbs
    trail = []
    curr = folder.parent
    while curr:
        trail.insert(0, curr)
        curr = curr.parent
        
    return render_template('folder_detail.html', folder=folder, section=section, subfolders=subfolder_data, drawings=drawings, trail=trail, q=search_query)

# Route: Serve Drawing Raw (for inline browser viewing)
@app.route('/drawing/<int:drawing_id>/raw')
def serve_drawing_raw(drawing_id):
    drawing = Drawing.query.get_or_404(drawing_id)
    
    # Determine mimetype
    filename = drawing.file_path or ""
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    mimetypes = {
        '.pdf': 'application/pdf',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.txt': 'text/plain',
    }
    mimetype = mimetypes.get(ext, 'application/octet-stream')

    # 1. Serve directly from database LargeBinary if available
    if drawing.file_data:
        import io
        return send_file(io.BytesIO(drawing.file_data), mimetype=mimetype, as_attachment=False)
        
    # 2. Fallback: Check local disk storage paths
    if not filename:
        abort(404)
        
    app_root = os.path.dirname(os.path.abspath(__file__))
    absolute_path = os.path.join(app_root, filename)
    if os.path.exists(absolute_path) and os.path.isfile(absolute_path):
        return send_file(absolute_path, mimetype=mimetype, as_attachment=False)
        
    abort(404)


# Route: Drawing Details
@app.route('/drawing/<int:drawing_id>')
def drawing_detail(drawing_id):
    drawing = Drawing.query.get_or_404(drawing_id)
    folder = Folder.query.get(drawing.folder_id)
    section = Section.query.get(folder.section_id)
    
    # Build folder trail list for drawing breadcrumbs
    trail = []
    curr = folder
    while curr:
        trail.insert(0, curr)
        curr = curr.parent
        
    # Extract file extension and build Excel preview if applicable
    file_ext = ""
    excel_preview_html = None
    if drawing.file_path:
        file_ext = os.path.splitext(drawing.file_path)[1].lower()
        
        if file_ext in ['.xls', '.xlsx']:
            app_root = os.path.dirname(os.path.abspath(__file__))
            absolute_path = os.path.join(app_root, drawing.file_path)
            if os.path.exists(absolute_path) and os.path.isfile(absolute_path):
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(absolute_path, read_only=True, data_only=True)
                    sheet = wb.active
                    
                    html = ['<div style="overflow-x:auto; max-height:450px; border-radius:12px; border:1px solid rgba(16,185,129,0.15); background:#fff; margin-bottom:1.5rem;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem; text-align:left;">']
                    
                    for r_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                        if r_idx >= 15:
                            break
                        bg_color = '#f8fafc' if r_idx == 0 else '#ffffff'
                        border_bottom = '2px solid #cbd5e1' if r_idx == 0 else '1px solid #e2e8f0'
                        font_weight = 'bold' if r_idx == 0 else 'normal'
                        
                        html.append(f'<tr style="background:{bg_color}; border-bottom:{border_bottom}; font-weight:{font_weight};">')
                        for cell in row:
                            val = str(cell) if cell is not None else ""
                            html.append(f'<td style="padding:0.75rem 1rem; border-right:1px solid #e2e8f0; white-space:nowrap; max-width:200px; overflow:hidden; text-overflow:ellipsis;">{val}</td>')
                        html.append('</tr>')
                        
                    html.append('</table></div>')
                    excel_preview_html = "".join(html)
                except Exception as e:
                    excel_preview_html = f'<div style="text-align:center; padding:2rem; background:rgba(255,255,255,0.7); border:1px dashed var(--border-color); border-radius:16px;"><p style="font-size:0.9rem; color:var(--text-muted); margin-bottom:1rem;">Live preview not available. Install openpyxl library to see direct Excel spreadsheet logs.</p><code style="font-size:0.8rem; background:#f1f5f9; padding:0.4rem 0.8rem; border-radius:6px; color:#2563eb;">pip install openpyxl</code></div>'
        
    return render_template('drawing_detail.html', drawing=drawing, folder=folder, section=section, trail=trail, file_ext=file_ext, excel_preview_html=excel_preview_html)

# Helper class to structure PDF generation in basic ASCII format
class PDFWriter:
    def __init__(self):
        self.objects = []
        self.offsets = []

    def add_object(self, content):
        obj_id = len(self.objects) + 1
        if isinstance(content, str):
            content = content.encode('ascii')
        self.objects.append(content)
        return obj_id

    def build(self):
        pdf = bytearray(b"%PDF-1.4\n")
        self.offsets = []
        
        for i, obj in enumerate(self.objects):
            obj_id = i + 1
            self.offsets.append(len(pdf))
            pdf.extend(f"{obj_id} 0 obj\n".encode('ascii'))
            pdf.extend(obj)
            pdf.extend(b"\nendobj\n")
            
        xref_start = len(pdf)
        pdf.extend(b"xref\n")
        pdf.extend(f"0 {len(self.objects) + 1}\n".encode('ascii'))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in self.offsets:
            pdf.extend(f"{offset:010d} 00000 n \n".encode('ascii'))
            
        pdf.extend(b"trailer\n")
        pdf.extend(f"<< /Size {len(self.objects) + 1} /Root 1 0 R >>\n".encode('ascii'))
        pdf.extend(b"startxref\n")
        pdf.extend(f"{xref_start}\n".encode('ascii'))
        pdf.extend(b"%%EOF")
        
        return bytes(pdf)


# Route: Download Drawing PDF Catalog Sheet or Actual Local File
@app.route('/download/<int:drawing_id>')
def download_drawing(drawing_id):
    drawing = Drawing.query.get_or_404(drawing_id)
    
    # 1. If file bytes are stored in the database, serve directly from DB LargeBinary
    if drawing.file_data:
        import io
        filename = os.path.basename(drawing.file_path) if drawing.file_path else f"drawing_{drawing.id}.bin"
        return send_file(io.BytesIO(drawing.file_data), as_attachment=True, download_name=filename)
        
    # 2. Fallback: If not migrated yet, check local filesystem paths
    if drawing.file_path:
        import os
        from flask import send_file
        
        # Path candidates to check on local filesystem
        path_candidates = [
            # Check absolute path
            drawing.file_path,
            # Check relative to app folder
            os.path.abspath(os.path.join(os.path.dirname(__file__), drawing.file_path)),
            # Check relative to Desktop digital library folder
            os.path.join(r"C:\Users\abhis\Desktop\CEMENT_PLANT_DIGITAL_LIBRARY_FINAL_EXACT", drawing.file_path),
            # Check relative to Desktop directory
            os.path.join(r"C:\Users\abhis\Desktop", drawing.file_path)
        ]
        
        # Add checks for nested subdirectories under the desktop digital library folder
        # such as inside Maintenance / COMMON MANUALS or Test Reports
        base_library = r"C:\Users\abhis\Desktop\CEMENT_PLANT_DIGITAL_LIBRARY_FINAL_EXACT"
        if os.path.exists(base_library):
            for root, dirs, files in os.walk(base_library):
                if os.path.basename(drawing.file_path) in files:
                    path_candidates.append(os.path.join(root, os.path.basename(drawing.file_path)))
                    break
                    
        for candidate in path_candidates:
            if os.path.exists(candidate) and os.path.isfile(candidate):
                # Serve the actual file!
                return send_file(candidate, as_attachment=True, download_name=os.path.basename(candidate))
                
    # 2. Fallback: Generate the text-only PDF metadata catalog sheet if the file is not found on disk
    date_str = drawing.created_at.strftime('%Y-%m-%d %H:%M')
    title_clean = drawing.title.encode('ascii', 'ignore').decode('ascii').replace("(", "\\(").replace(")", "\\)")
    num_clean = drawing.drawing_number.encode('ascii', 'ignore').decode('ascii').replace("(", "\\(").replace(")", "\\)")
    rev_clean = drawing.revision.encode('ascii', 'ignore').decode('ascii').replace("(", "\\(").replace(")", "\\)")
    desc_clean = (drawing.description or 'No description provided.').encode('ascii', 'ignore').decode('ascii').replace("(", "\\(").replace(")", "\\)")
    
    writer = PDFWriter()
    
    # 1. Catalog Object (root node of the PDF tree)
    catalog_id = writer.add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    
    # 2. Pages Object (container for all pages in document)
    pages_id = writer.add_object(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    
    # 3. Page Object (defines page parents, dimensions, fonts, and contents)
    page_id = writer.add_object(
        b"<< /Type /Page /Parent 2 0 R "
        b"/Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
    )
    
    # 4. Font Object (standard Helvetica font setup)
    font_id = writer.add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    
    # 5. Content Stream Object (drawing text commands)
    text_commands = [
        b"BT",
        b"/F1 20 Tf",
        b"50 720 Td",
        b"(DOCUVAULT DRAWING ARCHIVE) Tj",
        b"0 -30 Td",
        b"(=========================================) Tj",
        b"/F1 13 Tf",
        b"0 -40 Td",
        f"(Drawing Number: {num_clean}) Tj".encode('ascii'),
        b"0 -25 Td",
        f"(Drawing Title: {title_clean}) Tj".encode('ascii'),
        b"0 -25 Td",
        f"(Revision: {rev_clean}) Tj".encode('ascii'),
        b"0 -25 Td",
        f"(Date Registered: {date_str}) Tj".encode('ascii'),
        b"0 -40 Td",
        f"(Description: {desc_clean[:65]}) Tj".encode('ascii'),
        b"ET"
    ]
    stream_content = b"\n".join(text_commands)
    
    content_id = writer.add_object(
        f"<< /Length {len(stream_content)} >>\nstream\n".encode('ascii') + 
        stream_content + 
        b"\nendstream"
    )
    
    pdf_data = writer.build()
    
    from flask import Response
    return Response(
        pdf_data,
        mimetype="application/pdf",
        headers={"Content-disposition": f"attachment; filename={drawing.title.replace(' ', '_')}.pdf"}
    )


# Route: Master Index Search Page
@app.route('/master-index')
def master_index():
    return render_template('master_index.html')


# Route: Global Search
@app.route('/search')
def global_search():
    query = request.args.get('q', '').strip()
    if not query:
        return render_template('search_results.html', query='', drawings=[], count=0)
    
    # Perform an optimized join query across Drawing, Folder, and Section
    # Limit results to 200 for extremely fast performance
    results = db.session.query(Drawing, Folder, Section).join(
        Folder, Drawing.folder_id == Folder.id
    ).join(
        Section, Folder.section_id == Section.id
    ).filter(
        (Drawing.title.ilike(f'%{query}%')) |
        (Drawing.drawing_number.ilike(f'%{query}%')) |
        (Drawing.description.ilike(f'%{query}%'))
    ).order_by(Drawing.title).limit(200).all()
    
    enriched_results = []
    for drawing, folder, section in results:
        enriched_results.append({
            'drawing': drawing,
            'folder': folder,
            'section': section
        })
        
    return render_template('search_results.html', query=query, results=enriched_results, count=len(results))

# Route: Add Content Forms
@app.route('/add', methods=['GET', 'POST'])
def add_item():
    type_param = request.args.get('type', 'section')
    if type_param not in ['section', 'folder', 'drawing']:
        type_param = 'section'
    
    if request.method == 'POST':
        item_type = request.form.get('item_type')
        
        if item_type == 'section':
            name = request.form.get('name')
            description = request.form.get('description')
            color_theme = request.form.get('color_theme', 'blue')
            
            if not name:
                flash("Section name is required!", "error")
                return redirect(url_for('add_item', type='section'))
                
            new_section = Section(name=name, description=description, color_theme=color_theme)
            try:
                db.session.add(new_section)
                db.session.commit()
                flash(f"Section '{name}' created successfully!", "success")
                return redirect(url_for('dashboard'))
            except Exception as e:
                db.session.rollback()
                flash(f"Error creating section: {str(e)}", "error")
                
        elif item_type == 'folder':
            name = request.form.get('name')
            description = request.form.get('description')
            section_id = request.form.get('section_id')
            
            if not name or not section_id:
                flash("Folder name and Section selection are required!", "error")
                return redirect(url_for('add_item', type='folder'))
                
            new_folder = Folder(name=name, description=description, section_id=int(section_id))
            try:
                db.session.add(new_folder)
                db.session.commit()
                flash(f"Folder '{name}' created successfully!", "success")
                return redirect(url_for('section_detail', section_id=section_id))
            except Exception as e:
                db.session.rollback()
                flash(f"Error creating folder: {str(e)}", "error")
                
        elif item_type == 'drawing':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            folder_id = request.form.get('folder_id')
            file = request.files.get('file')
            
            if not title or not folder_id or not file or not file.filename.strip():
                flash("Drawing Title, Folder selection, and File upload are required!", "error")
                return redirect(url_for('add_item', type='drawing'))
                
            filename = file.filename.strip()
            drawing_number = f"{filename} ({folder_id})"
            
            # Check unique drawing number
            existing = Drawing.query.filter_by(drawing_number=drawing_number).first()
            if existing:
                flash(f"A file named '{filename}' is already uploaded in this folder!", "error")
                return redirect(url_for('add_item', type='drawing'))
                
            # Reconstruct folder physical path under project storage/
            def get_folder_trail_path(f_id):
                f = db.session.get(Folder, f_id)
                if not f:
                    return ""
                parts = [f.name]
                curr = f
                while curr.parent_id:
                    curr = db.session.get(Folder, curr.parent_id)
                    if not curr:
                        break
                    parts.insert(0, curr.name)
                sec = db.session.get(Section, f.section_id)
                if sec:
                    parts.insert(0, sec.name)
                return os.path.join(*parts)
                
            folder_rel_path = os.path.join("storage", get_folder_trail_path(int(folder_id)))
            try:
                # Read raw binary bytes from the uploaded file
                file_bytes = file.read()
                db_file_path = os.path.join(folder_rel_path, filename)
                
                new_drawing = Drawing(
                    title=title,
                    drawing_number=drawing_number,
                    revision="00",
                    description=description,
                    folder_id=int(folder_id),
                    file_path=db_file_path,
                    file_data=file_bytes
                )
                db.session.add(new_drawing)
                db.session.commit()
                flash(f"Drawing '{filename}' added successfully to database storage!", "success")
                return redirect(url_for('folder_detail', folder_id=folder_id))
            except Exception as e:
                db.session.rollback()
                flash(f"Error adding drawing: {str(e)}", "error")
                return redirect(url_for('add_item', type='drawing'))
                
        return redirect(url_for('dashboard'))
        
    # GET Request: Prepare forms list
    all_sections = Section.query.order_by(Section.name).all()
    all_folders = Folder.query.order_by(Folder.name).all()
    
    # Group folders by section for easy UI selection
    folders_by_section = {}
    for folder in all_folders:
        sec = Section.query.get(folder.section_id)
        if sec:
            if sec.name not in folders_by_section:
                folders_by_section[sec.name] = []
            folders_by_section[sec.name].append(folder)

    return render_template('create.html', type=type_param, sections=all_sections, folders_by_section=folders_by_section)


# Route: Admin Upload Drawing
@app.route('/folder/<int:folder_id>/upload', methods=['POST'])
def upload_drawing(folder_id):
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin:
        flash("Admin permissions required.", "error")
        return redirect(url_for('dashboard'))
        
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    file = request.files.get('file')
    
    if not title or not file or not file.filename.strip():
        flash("Drawing Title and File upload are required!", "error")
        return redirect(url_for('folder_detail', folder_id=folder_id))
        
    filename = file.filename.strip()
    
    # Check unique drawing number
    drawing_number = f"{filename} ({folder_id})"
    existing = Drawing.query.filter_by(drawing_number=drawing_number).first()
    if existing:
        flash(f"A file named '{filename}' is already uploaded in this folder!", "error")
        return redirect(url_for('folder_detail', folder_id=folder_id))
        
    # Reconstruct folder physical path under project storage/
    def get_folder_trail_path(f_id):
        f = db.session.get(Folder, f_id)
        if not f:
            return ""
        parts = [f.name]
        curr = f
        while curr.parent_id:
            curr = db.session.get(Folder, curr.parent_id)
            if not curr:
                break
            parts.insert(0, curr.name)
        sec = db.session.get(Section, f.section_id)
        if sec:
            parts.insert(0, sec.name)
        return os.path.join(*parts)
        
    folder_rel_path = os.path.join("storage", get_folder_trail_path(folder_id))
    try:
        # Read raw binary bytes from the uploaded file
        file_bytes = file.read()
        db_file_path = os.path.join(folder_rel_path, filename)
        
        new_drawing = Drawing(
            title=title,
            drawing_number=drawing_number,
            revision="00",
            description=description,
            folder_id=folder_id,
            file_path=db_file_path,
            file_data=file_bytes
        )
        db.session.add(new_drawing)
        db.session.commit()
        flash(f"Drawing '{filename}' uploaded successfully to database storage!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error uploading drawing: {str(e)}", "error")
        
    return redirect(url_for('folder_detail', folder_id=folder_id))


# Route: Admin Edit Drawing
@app.route('/drawing/<int:drawing_id>/edit', methods=['POST'])
def edit_drawing(drawing_id):
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin:
        flash("Admin permissions required.", "error")
        return redirect(url_for('dashboard'))
        
    drawing = Drawing.query.get_or_404(drawing_id)
    title = request.form.get('title', '').strip()
    revision = request.form.get('revision', '').strip()
    description = request.form.get('description', '').strip()
    file = request.files.get('file')
    
    if not title:
        flash("Drawing Title is required!", "error")
        return redirect(url_for('drawing_detail', drawing_id=drawing_id))
        
    drawing.title = title
    drawing.revision = revision
    drawing.description = description
    
    # Handle file replacement if a new file is uploaded
    if file and file.filename.strip():
        filename = file.filename.strip()
        
        # Reconstruct folder physical path
        def get_folder_trail_path(f_id):
            f = db.session.get(Folder, f_id)
            if not f:
                return ""
            parts = [f.name]
            curr = f
            while curr.parent_id:
                curr = db.session.get(Folder, curr.parent_id)
                if not curr:
                    break
                parts.insert(0, curr.name)
            sec = db.session.get(Section, f.section_id)
            if sec:
                parts.insert(0, sec.name)
            return os.path.join(*parts)
            
        folder_rel_path = os.path.join("storage", get_folder_trail_path(drawing.folder_id))
        try:
            # Read raw binary bytes from the uploaded file
            file_bytes = file.read()
            drawing.file_data = file_bytes
            drawing.file_path = os.path.join(folder_rel_path, filename)
            drawing.drawing_number = f"{filename} ({drawing.folder_id})"
        except Exception as e:
            flash(f"Error reading file bytes: {str(e)}", "error")
            return redirect(url_for('drawing_detail', drawing_id=drawing_id))
            
    try:
        db.session.commit()
        flash("Drawing updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating database: {str(e)}", "error")
        
    return redirect(url_for('drawing_detail', drawing_id=drawing_id))


# Route: Admin Delete Drawing
@app.route('/drawing/<int:drawing_id>/delete', methods=['POST'])
def delete_drawing(drawing_id):
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin:
        flash("Admin permissions required.", "error")
        return redirect(url_for('dashboard'))
        
    drawing = Drawing.query.get_or_404(drawing_id)
    folder_id = drawing.folder_id
    
    # No physical file deletion needed as data resides purely in database BLOB storage
                
    try:
        db.session.delete(drawing)
        db.session.commit()
        flash("Drawing deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting drawing from database: {str(e)}", "error")
        
    return redirect(url_for('folder_detail', folder_id=folder_id))


# AJAX API: Get top-level folders in a section
@app.route('/api/sections/<int:section_id>/folders')
def api_section_folders(section_id):
    user = db.session.get(User, session.get('user_id'))
    if not user or not user.is_admin:
        return jsonify([]), 403
    # Get top-level folders (where parent_id is NULL)
    folders = Folder.query.filter_by(section_id=section_id, parent_id=None).order_by(Folder.name).all()
    return jsonify([{
        'id': f.id,
        'name': f.name,
        'has_subfolders': Folder.query.filter_by(parent_id=f.id).first() is not None
    } for f in folders])


# AJAX API: Get subfolders in a folder
@app.route('/api/folders/<int:folder_id>/subfolders')
def api_subfolders(folder_id):
    user = db.session.get(User, session.get('user_id'))
    if not user or not user.is_admin:
        return jsonify([]), 403
    # Get nested folders
    folders = Folder.query.filter_by(parent_id=folder_id).order_by(Folder.name).all()
    return jsonify([{
        'id': f.id,
        'name': f.name,
        'has_subfolders': Folder.query.filter_by(parent_id=f.id).first() is not None
    } for f in folders])


# Route: Download Master Demo Excel Index
@app.route('/download_demo_excel')
def download_demo_excel():
    user = db.session.get(User, session.get('user_id'))
    if not user:
        abort(403)
        
    # Retrieve the Demo Index Excel record from the database by drawing number pattern
    drawing = Drawing.query.filter(Drawing.drawing_number.like('Demo_Drawing_Index.xlsx%')).first()
    if drawing and drawing.file_data:
        import io
        return send_file(io.BytesIO(drawing.file_data), as_attachment=True, download_name='Demo_Drawing_Index.xlsx')
    abort(404)


# Run Server locally
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
