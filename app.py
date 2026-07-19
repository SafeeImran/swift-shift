import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'super_secret_production_key_safe'

# Hardcoded destructive-action password required for delete routes,
# disable/enable cars, and editing car records.
DESTRUCTIVE_PASSWORD = "miz1234!"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static/uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CURRENT_FUEL_RATE = 380.00

# Master Authorized Multi-Tenant Configuration Mapping Matrix
COMPANY_MAP = {
    "AGI": {"prefix": "AG", "db": "AGI.db"},
    "Utopia": {"prefix": "UT", "db": "Utopia.db"},
    "SastaTicket": {"prefix": "ST", "db": "SastaTicket.db"},
    "Reckitt Benckiser": {"prefix": "RB", "db": "Reckitt_Benckiser.db"},
    "Supportify": {"prefix": "SP", "db": "Supportify.db"},
    "Bol Channel": {"prefix": "BO", "db": "Bol_Channel.db"},
    "Matcom Food" : {"prefix" : "MF", "db": "Matcom_Food.db"},
    "Verticle Edge" : {"prefix" : "VE", "db": "Vertical_edge.db"}
}

def get_db_path(company_name):
    db_filename = COMPANY_MAP.get(company_name, {"db": "database.db"})["db"]
    return os.path.join(BASE_DIR, db_filename)

def init_specific_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS routes (
            route_name TEXT PRIMARY KEY,
            company_name TEXT,
            kms REAL,
            fuel_cons_per_km REAL,
            working_days INTEGER,
            fuel_in_litres REAL,
            route_rental REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cars (
            registration_no TEXT PRIMARY KEY,
            password TEXT,
            captain_name TEXT,
            contact1 TEXT,
            contact2 TEXT,
            driver_code TEXT,
            route_name TEXT,
            ac_status TEXT,
            car_model TEXT,
            cnic_front TEXT,
            cnic_back TEXT,
            license_front TEXT,
            license_back TEXT,
            advance_amount INTEGER DEFAULT 0,
            advance_timestamp TEXT,
            day_off_reason TEXT,
            day_off_timestamp TEXT,
            actual_fuel_taken REAL DEFAULT 0,
            custom_rental REAL DEFAULT NULL,
            custom_kms REAL DEFAULT NULL,
            custom_working_days REAL DEFAULT NULL,
            backup_total REAL DEFAULT 0,
            is_disabled INTEGER DEFAULT 0
        )
    ''');

    #  PERMANENT HISTORICAL LEDGER TRANSACTION TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_no TEXT,
            timestamp TEXT,
            entry_type TEXT, -- 'Daily Update', 'Advance Request', 'Attendance Change', 'Admin Correction'
            fuel_added REAL DEFAULT 0,
            backup_added REAL DEFAULT 0,
            advance_added REAL DEFAULT 0,
            description TEXT
        )
    ''')

    # Lightweight schema migration: add columns that predate the current schema.
    # CREATE TABLE IF NOT EXISTS is a no-op for tables that already exist, so any
    # column added later must be backfilled here, otherwise old DB files will be
    # missing the column and any query that references it will crash.
    cursor.execute("PRAGMA table_info(cars)")
    existing_cars_cols = {row[1] for row in cursor.fetchall()}
    if 'backup_total' not in existing_cars_cols:
        cursor.execute("ALTER TABLE cars ADD COLUMN backup_total REAL DEFAULT 0")
    if 'is_disabled' not in existing_cars_cols:
        cursor.execute("ALTER TABLE cars ADD COLUMN is_disabled INTEGER DEFAULT 0")

    #is_fixed
    cursor.execute("PRAGMA table_info(routes)")
    existing_routes_cols = {row[1] for row in cursor.fetchall()}
    if 'is_fixed' not in existing_routes_cols:
        cursor.execute("ALTER TABLE routes ADD COLUMN is_fixed INTEGER DEFAULT 0")
    conn.commit()
    conn.close()

# Enforce initialization across all separate files on startup
for comp in COMPANY_MAP:
    init_specific_db(get_db_path(comp))

def check_and_reset_limits_multi(car_data, db_path):
    # If tuple layout changes, map via indexed values safely
    reg = car_data[0]
    adv_time = car_data[14]
    off_time = car_data[16]
    now = datetime.now()
    updated = False

    if adv_time:
        try:
            if now - datetime.strptime(adv_time, "%Y-%m-%d %H:%M:%S") >= timedelta(days=1):
                conn = sqlite3.connect(db_path)
                conn.cursor().execute("UPDATE cars SET advance_amount=0, advance_timestamp=NULL WHERE registration_no=?", (reg,))
                conn.commit()
                conn.close()
                updated = True
        except: pass

    if off_time:
        try:
            if now - datetime.strptime(off_time, "%Y-%m-%d %H:%M:%S") >= timedelta(days=2):
                conn = sqlite3.connect(db_path)
                conn.cursor().execute("UPDATE cars SET day_off_reason=NULL, day_off_timestamp=NULL WHERE registration_no=?", (reg,))
                conn.commit()
                conn.close()
                updated = True
        except: pass

    if updated:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cars WHERE registration_no=?", (reg,))
        car_data = cursor.fetchone()
        conn.close()

    return car_data

# --- CASCADING DROPDOWN API ENDPOINT ---
@app.route('/api/get_routes/<company_name>')
def api_get_routes(company_name):
    if company_name not in COMPANY_MAP:
        return jsonify([])
    db_p = get_db_path(company_name)
    conn = sqlite3.connect(db_p)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT route_name FROM routes")
        routes = [r[0] for r in cursor.fetchall()]
    except:
        routes = []
    conn.close()
    return jsonify(routes)

# --- CAPTAIN / USER INTERFACE ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if 'user' not in session:
        if request.method == 'POST':
            reg_no = request.form['registration_no'].strip().upper()
            pwd = request.form['password'].strip()

            for comp in COMPANY_MAP.keys():
                db_p = get_db_path(comp)
                conn = sqlite3.connect(db_p)
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT * FROM cars WHERE registration_no=? AND password=? AND IFNULL(is_disabled,0)=0", (reg_no, pwd))
                    user = cursor.fetchone()
                except sqlite3.OperationalError:
                    user = None
                conn.close()
                if user:
                    session['user'] = reg_no
                    session['user_company'] = comp
                    return redirect(url_for('index'))

            flash("Invalid Details / معلومات غلط ہیں")
        return render_template('index.html', logged_in=False)

    comp_context = session.get('user_company')
    db_path = get_db_path(comp_context)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cars WHERE registration_no=?", (session['user'],))
    car = cursor.fetchone()

    if not car:
        session.pop('user', None)
        return redirect(url_for('index'))

    car = check_and_reset_limits_multi(car, db_path)

    # car index fields fallback structure mapping reference
    cursor.execute("SELECT company_name, kms, fuel_cons_per_km, working_days, fuel_in_litres, route_rental FROM routes WHERE route_name=?", (car[6],))
    route = cursor.fetchone()
    conn.close()

    if not route:
        route = (comp_context, 0.0, 1.0, 0, 0.0, 0.0)

    active_kms = car[19] if car[19] is not None else route[1]
    active_working_days = car[20] if car[20] is not None else route[3]
    active_rental = car[18] if car[18] is not None else route[5]
    accumulated_backup = car[21] if (len(car) > 21 and car[21] is not None) else 0

    telemetry = {
        'registration_no': car[0],
        'captain_name': car[2],
        'contact1': car[3],
        'contact2': car[4] if car[4] else "Not Provided",
        'driver_code': car[5],
        'route_name': car[6] if car[6] else "None Assigned",
        'ac_status': car[7],
        'car_model': car[8],
        'advance_amount': car[13] if car[13] else 0,
        'day_off_reason': car[15],
        'actual_fuel_taken_by_captain_in_litres': car[17] if car[17] else 0,
        'company_name': comp_context,
        'kms': active_kms,
        'fuel_cons_per_km': route[2],
        'working_days': active_working_days,
        'route_rental': active_rental,
        'current_fuel_rate': CURRENT_FUEL_RATE,
        'backup': accumulated_backup
    }

    telemetry['fuelinlitre'] = round(telemetry['kms'] / telemetry['fuel_cons_per_km'], 2) if telemetry['fuel_cons_per_km'] > 0 else 0
    telemetry['monthly_litre'] = round(telemetry['working_days'] * telemetry['fuelinlitre'], 2)
    telemetry['monthly_fuel_amount'] = round(telemetry['monthly_litre'] * CURRENT_FUEL_RATE, 2)
    telemetry['fuel_diff'] = round(telemetry['monthly_litre'] - telemetry['actual_fuel_taken_by_captain_in_litres'], 2)
    telemetry['fuel_diff_amount'] = round(telemetry['fuel_diff'] * CURRENT_FUEL_RATE, 2)
    telemetry['captain_payment'] = round(telemetry['route_rental'] + telemetry['monthly_fuel_amount'] - telemetry['backup'] - telemetry['fuel_diff_amount'] - telemetry['advance_amount'], 2)

    return render_template('index.html', logged_in=True, data=telemetry)

@app.route('/confirm', methods=['POST'])
def confirm():
    if 'user' not in session: return redirect(url_for('index'))
    reg_no = session['user']
    db_path = get_db_path(session.get('user_company'))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if 'advance_check' in request.form:
        amt = request.form.get('advance_select')
        cursor.execute("UPDATE cars SET advance_amount=?, advance_timestamp=? WHERE registration_no=?", (amt, now_str, reg_no))
        cursor.execute("""
            INSERT INTO transaction_logs (registration_no, timestamp, entry_type, advance_added, description)
            VALUES (?, ?, 'Advance Request', ?, ?)
        """, (reg_no, now_str, float(amt), f"Driver verified advance withdrawal request: Rs.{amt}"))

    if 'dayoff_check' in request.form:
        sel = request.form.get('day_off_reason_select')
        reason = request.form.get('day_off_reason_custom', 'Other').strip() if sel == 'Others' else sel
        cursor.execute("UPDATE cars SET day_off_reason=?, day_off_timestamp=? WHERE registration_no=?", (reason, now_str, reg_no))
        cursor.execute("""
            INSERT INTO transaction_logs (registration_no, timestamp, entry_type, description)
            VALUES (?, ?, 'Attendance Change', ?)
        """, (reg_no, now_str, f"Driver filed status changes to code: {reason}"))
        flash("Day off saved successfully.", "dayoff_success")
    conn.commit()
    conn.close()
    return redirect(url_for('index'))


# --- ADMINISTRATIVE WORKSPACE ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_portal():
    if 'admin_logged_in' not in session:
        if request.method == 'POST':
            if request.form.get('username') == "admin" and request.form.get('password') == "admin123":
                session['admin_logged_in'] = True
                return redirect(url_for('admin_portal'))
            flash("Invalid Admin Credentials")
        return render_template('admin.html', logged_in=False)
    return render_template('admin.html', logged_in=True)

@app.route('/admin/add_route', methods=['GET', 'POST'])
def add_route():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))
    if request.method == 'POST':
        base_name = request.form.get('route_name').strip()
        comp = request.form.get('company_name')
        kms = float(request.form.get('kms', 0))
        cons = float(request.form.get('fuel_cons_per_km', 1))
        days = int(request.form.get('working_days', 0))
        rental = float(request.form.get('route_rental', 0))
        is_fixed = 1 if request.form.get('is_fixed_route') else 0

        db_path = get_db_path(comp)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT route_name FROM routes WHERE route_name LIKE ?", (f"{base_name}%",))
        existing_matches = [r[0] for r in cursor.fetchall()]

        final_route_name = base_name
        if len(existing_matches) > 0:
            count = len(existing_matches)
            final_route_name = f"{base_name} {count + 1}"
            flash(f"⚠️ Notice: {count} version(s) of '{base_name}' already existed. Auto-named this entry to '{final_route_name}'.", "warning")
        # --- Inside app.route('/admin/add_route') POST block ---
        # is_fixed = request.form.get('is_fixed_route') == '1'
        # kms = 0.0 if is_fixed else float(request.form.get('kms', 0))
        derived_litres = round(kms / cons, 2) if cons > 0 else 0
        try:
            cursor.execute("INSERT INTO routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (final_route_name, comp, kms, cons, days, derived_litres, rental, is_fixed))
            conn.commit()
            flash(f"Route '{final_route_name}' successfully added to {comp} partition!", "success")
        except Exception as e:
            flash(f"Error saving route layout structure: {e}", "error")
        conn.close()

    return render_template('add_route.html', companies=COMPANY_MAP.keys())

@app.route('/admin/add_vehicle', methods=['GET', 'POST'])
def add_vehicle():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    if request.method == 'POST':
        comp = request.form.get('company_select')
        reg = request.form.get('registration_no').strip()
        pwd = request.form.get('password').strip()
        cap = request.form.get('captain_name').strip()
        c1 = request.form.get('contact1').strip()
        c2 = request.form.get('contact2').strip()
        route = request.form.get('route_assigned')
        ac = request.form.get('ac_status')
        model = request.form.get('car_model')

        if len(c1) != 11 or not c1.isdigit():
            flash("❌ Validation Error: Primary Contact number must be exactly 11 numeric digits.", "error")
            return redirect(url_for('add_vehicle'))
        if c2 and (len(c2) != 11 or not c2.isdigit()):
            flash("❌ Validation Error: Secondary Contact number must be exactly 11 numeric digits.", "error")
            return redirect(url_for('add_vehicle'))
        if not route:
            flash("❌ Route allocation is mandatory. Please select a valid route profile.", "error")
            return redirect(url_for('add_vehicle'))

        for cross_comp in COMPANY_MAP.keys():
            cross_db = get_db_path(cross_comp)
            cross_conn = sqlite3.connect(cross_db)
            cross_cursor = cross_conn.cursor()
            cross_cursor.execute("SELECT registration_no FROM cars WHERE registration_no=?", (reg,))
            duplicate_found = cross_cursor.fetchone()
            cross_conn.close()
            if duplicate_found:
                flash(f"❌ System Duplicate Error: Registration Number '{reg}' already exists under '{cross_comp}'.", "error")
                return redirect(url_for('add_vehicle'))

        db_path = get_db_path(comp)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cars")
        car_count = cursor.fetchone()[0] + 1
        prefix_code = COMPANY_MAP[comp]['prefix']
        generated_code = f"{prefix_code}-{str(car_count).zfill(2)}"

        def save_media(field_name):
            file = request.files.get(field_name)
            if file and file.filename != '':
                ext = secure_filename(file.filename).split('.')[-1]
                filename = f"{reg}_{field_name}.{ext}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                return filename
            return None

        cf, cb = save_media('cnic_front'), save_media('cnic_back')
        lf, lb = save_media('license_front'), save_media('license_back')

        try:
            cursor.execute("""
                INSERT INTO cars (registration_no, password, captain_name, contact1, contact2, driver_code,
                                  route_name, ac_status, car_model, cnic_front, cnic_back, license_front, license_back, backup_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (reg, pwd, cap, c1, c2, generated_code, route, ac, model, cf, cb, lf, lb))
            conn.commit()
            flash(f"Vehicle registered with Fleet Code Identifier: {generated_code}", "success")
        except Exception as e:
            flash(f"Enrollment Fault: {e}", "error")
        conn.close()

    return render_template('add_vehicle.html', companies=COMPANY_MAP.keys())

@app.route('/admin/view_routes', methods=['GET', 'POST'])
def view_routes():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    if request.method == 'POST' and request.form.get('action') == 'delete_route':
        # Server-side password verification for destructive operation
        supplied = request.form.get('verify_password', '')
        if supplied != DESTRUCTIVE_PASSWORD:
            flash("❌ ACCESS DENIED: Invalid management passphrase. Route deletion aborted.", "error")
            return redirect(url_for('view_routes'))

        r_name = (request.form.get('route_name') or '').strip()
        comp_context = (request.form.get('company_name') or '').strip()

        # Tolerate dash/empty company name — if the form didn't supply a valid
        # company, refuse rather than crash on a bad DB path.
        if not comp_context or comp_context in ('-', '—') or comp_context not in COMPANY_MAP:
            flash("❌ Cannot determine the corporate context for this route. Aborted.", "error")
            return redirect(url_for('view_routes'))
        if not r_name or r_name in ('-', '—'):
            flash("❌ Cannot determine the route name. Aborted.", "error")
            return redirect(url_for('view_routes'))

        db_p = get_db_path(comp_context)
        conn = sqlite3.connect(db_p)
        cursor = conn.cursor()
        try:
            # Case-insensitive delete so capitalization doesn't matter
            cursor.execute("DELETE FROM routes WHERE LOWER(route_name)=LOWER(?)", (r_name,))
            cursor.execute("UPDATE cars SET route_name=NULL WHERE LOWER(route_name)=LOWER(?)", (r_name,))
            conn.commit()
            flash(f"🗑️ Route '{r_name}' successfully purged from {comp_context} database.", "success")
        except Exception as e:
            flash(f"❌ Deletion failed: {e}", "error")
        conn.close()
        return redirect(url_for('view_routes'))

    # Compile all routes. The 'routes' table may not contain every route the
    # fleet references — captains often have free-text 'cars.route_name' values
    # with no corresponding 'routes' row, and we still want to surface those
    # so they can be calibrated or deleted.
    merged = {}  # key = (company, lowercase route_name) -> dict

    def _merge(comp, route_name, kms=None, fuel_cons=None, working_days=None,
               route_rental=None, in_routes_table=False, car_count=0):
        key = (comp, (route_name or '').strip().lower())
        if not key[1]:
            return
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                'route_name': route_name,
                'company_name': comp,
                'kms': kms,
                'fuel_cons_per_km': fuel_cons,
                'working_days': working_days,
                'route_rental': route_rental,
                'in_routes_table': in_routes_table,
                'car_count': car_count,
            }
        else:
            # Prefer real data over None
            if existing['kms'] is None and kms is not None: existing['kms'] = kms
            if existing['fuel_cons_per_km'] is None and fuel_cons is not None: existing['fuel_cons_per_km'] = fuel_cons
            if existing['working_days'] is None and working_days is not None: existing['working_days'] = working_days
            if existing['route_rental'] is None and route_rental is not None: existing['route_rental'] = route_rental
            if in_routes_table: existing['in_routes_table'] = True
            existing['car_count'] += car_count

    for comp in COMPANY_MAP.keys():
        db_p = get_db_path(comp)
        conn = sqlite3.connect(db_p)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 1. Routes that exist in the canonical routes table
        try:
            cursor.execute("SELECT * FROM routes")
            for row in cursor.fetchall():
                _merge(comp, row['route_name'],
                       kms=row['kms'], fuel_cons=row['fuel_cons_per_km'],
                       working_days=row['working_days'],
                       route_rental=row['route_rental'],
                       in_routes_table=True)
        except sqlite3.OperationalError:
            pass

        # 2. Distinct route names referenced by cars that aren't in the routes table
        try:
            cursor.execute(
                "SELECT route_name, COUNT(*) FROM cars "
                "WHERE route_name IS NOT NULL AND TRIM(route_name) != '' "
                "AND route_name NOT IN ('-', '—') "
                "GROUP BY LOWER(route_name)"
            )
            for rname, cnt in cursor.fetchall():
                _merge(comp, rname, car_count=cnt)
        except sqlite3.OperationalError:
            pass


        conn.close()

    # Sort: company first, then route name
    all_compiled_routes = sorted(merged.values(), key=lambda x: (x['company_name'], (x['route_name'] or '').lower()))
    return render_template('view_routes.html', routes=all_compiled_routes)

@app.route('/admin/edit_entities', methods=['GET', 'POST'])
def edit_entities():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    if 'admin_verified_for_edit' not in session:
        if request.method == 'POST' and request.form.get('action') == 'verify_password':
            if request.form.get('secure_pass') == "admin123":
                session['admin_verified_for_edit'] = True
                return redirect(url_for('edit_entities'))
            else:
                flash("Security verification failed. Password invalid.")
        return render_template('edit_verification.html')

    selected_car = None
    selected_route = None
    routes_list = []
    found_company = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'search':
            search_target = request.form.get('search_target', '').strip().upper()

            for comp in COMPANY_MAP.keys():
                db_p = get_db_path(comp)
                conn = sqlite3.connect(db_p)
                cursor = conn.cursor()

                try:
                    cursor.execute("SELECT registration_no, actual_fuel_taken, advance_amount, contact1, contact2, custom_rental, route_name, custom_kms, custom_working_days FROM cars WHERE registration_no=?", (search_target,))
                    selected_car = cursor.fetchone()
                except sqlite3.OperationalError:
                    selected_car = None

                if not selected_car:
                    try:
                        cursor.execute("SELECT route_name, kms, working_days, route_rental, company_name, fuel_cons_per_km FROM routes WHERE route_name=?", (search_target,))
                        selected_route = cursor.fetchone()
                    except sqlite3.OperationalError:
                        selected_route = None

                if selected_car or selected_route:
                    found_company = comp
                    cursor.execute("SELECT route_name FROM routes")
                    routes_list = [r[0] for r in cursor.fetchall()]
                    conn.close()
                    break
                conn.close()

            if not selected_car and not selected_route:
                flash("No matching profile discovered within any corporate partitions.")

        elif action == 'update_vehicle':
            # Server-side password verification before mutating a car record
            supplied = request.form.get('verify_password', '')
            if supplied != DESTRUCTIVE_PASSWORD:
                flash("❌ ACCESS DENIED: Invalid management passphrase. Vehicle edit aborted.", "error")
                return redirect(url_for('edit_entities'))

            target_reg = request.form.get('target_reg_no')
            comp_context = request.form.get('comp_context')
            c1 = request.form.get('contact1', '').strip()
            c2 = request.form.get('contact2', '').strip()
            r_name = request.form.get('route_name')
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 🛠️ HELPER: Safe numerical parsing validator to intercept forbidden entries
            def parse_numeric(val, field_name, is_integer=False):
                if val is None or str(val).strip() == '':
                    return None
                try:
                    # Clean the string representation
                    cleaned_val = str(val).strip()
                    if is_integer:
                        return int(float(cleaned_val)) # Handles cases where float strings like "15.0" are sent to an int field
                    return float(cleaned_val)
                except (ValueError, TypeError):
                    raise ValueError(f"❌ Forbidden Value Exception: '{val}' is an invalid entry format for field '{field_name}'.")

            # Validate numerical parameters thoroughly
            try:
                fuel = parse_numeric(request.form.get('actual_fuel_taken', 0), 'Fuel Taken') or 0.0
                adv = parse_numeric(request.form.get('advance_amount', 0), 'Advance Amount', is_integer=True) or 0
                rent_val = parse_numeric(request.form.get('custom_rental'), 'Custom Rental')

                # Fixed: Properly capturing and validating both float and int types for kms_val and days_val
                kms_val = parse_numeric(request.form.get('custom_kms'), 'Custom Kms')
                days_val = parse_numeric(request.form.get('custom_working_days'), 'Custom Working Days')
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for('edit_entities'))

            if len(c1) != 11 or not c1.isdigit() or (c2 and (len(c2) != 11 or not c2.isdigit())):
                flash("❌ Operational Error: Phone numbers must be exactly 11 numeric digits.")
                return redirect(url_for('edit_entities'))

            db_p = get_db_path(comp_context)
            conn = sqlite3.connect(db_p)
            cursor = conn.cursor()

            # Get old values to construct structural history diff log
            cursor.execute("SELECT actual_fuel_taken, advance_amount FROM cars WHERE registration_no=?", (target_reg,))
            old_row = cursor.fetchone()
            old_fuel = old_row[0] if old_row else 0
            old_adv = old_row[1] if old_row else 0

            cursor.execute("""
                UPDATE cars SET actual_fuel_taken=?, advance_amount=?, contact1=?, contact2=?,
                                custom_rental=?, route_name=?, custom_kms=?, custom_working_days=?
                WHERE registration_no=?
            """, (fuel, adv, c1, c2, rent_val, r_name, kms_val, days_val, target_reg))

            # Log adjustment history row record receipt
            cursor.execute("""
                INSERT INTO transaction_logs (registration_no, timestamp, entry_type, fuel_added, advance_added, description)
                VALUES (?, ?, 'Admin Correction', ?, ?, ?)
            """, (target_reg, now_str, fuel - old_fuel, float(adv - old_adv), f"Admin Manual parameters adjustment overrides saved."))

            conn.commit()
            conn.close()
            flash("Vehicle operational parameters updated successfully!")

    return render_template('edit_entities.html', car=selected_car, route=selected_route, routes=routes_list, comp_context=found_company)

# --- ADVANCED INTELLIGENT ANALYTICS & SEARCH ENGINE ---
def generate_master_ledger(filter_company, filter_route, search_query, filter_attendance):
    compiled_master_ledger = []
    for comp in COMPANY_MAP.keys():
        if filter_company and filter_company != comp:
            continue

        db_p = get_db_path(comp)
        conn = sqlite3.connect(db_p)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM routes")
        r_rows = cursor.fetchall()
        routes_dict = {r['route_name']: dict(r) for r in r_rows}

        try:
            cursor.execute("SELECT * FROM cars")
            c_rows = cursor.fetchall()
        except sqlite3.OperationalError:
            c_rows = []
        conn.close()

        for c in c_rows:
            car = dict(c)
            r_name = car['route_name']
            r_data = routes_dict.get(r_name, {'company_name': comp, 'kms':0.0, 'fuel_cons_per_km':10.0, 'working_days':0, 'route_rental':0.0})

            base_kms = car['custom_kms'] if car['custom_kms'] is not None else r_data['kms']
            wd = car['custom_working_days'] if car['custom_working_days'] is not None else r_data['working_days']
            base_rental = car['custom_rental'] if car['custom_rental'] is not None else r_data['route_rental']
            is_fixed_route = r_data.get('is_fixed', 0) == 1
            # 🔍 DETECT FIXED VALUE ROUTE MATRIX FLAGS
            fuelinlitre = round(base_kms / r_data['fuel_cons_per_km'], 2) if r_data['fuel_cons_per_km'] > 0 else 0
            monthly_litre = round(wd * fuelinlitre, 2)
            monthly_fuel_amount = round(monthly_litre * CURRENT_FUEL_RATE, 2)
            backup = car['backup_total'] if ('backup_total' in car and car['backup_total'] is not None) else 0
            fuel_diff = round(monthly_litre - (car['actual_fuel_taken'] or 0), 2)
            fuel_diff_amount = round(fuel_diff * CURRENT_FUEL_RATE, 2)
            # If base_kms is 0, it means the admin checked "Is Fixed Value Route"
            if is_fixed_route:
                display_rental = 0.0
                calculated_route_cost = base_rental
                actual_fuel_taken = car['actual_fuel_taken'] or 0
                variance_litre = round(monthly_litre - actual_fuel_taken, 2)
                variance_penalty = round(variance_litre * CURRENT_FUEL_RATE, 2)

            else:
                # 🔄 STANDARD CALCULATING FLOW FOR TRADITIONAL MATRIX PROJECTILES
                display_rental = base_rental
                fuelinlitre = round(base_kms / r_data['fuel_cons_per_km'], 2) if r_data['fuel_cons_per_km'] > 0 else 0
                monthly_litre = round(wd * fuelinlitre, 2)
                calculated_route_cost = round(display_rental + (monthly_litre * CURRENT_FUEL_RATE), 2)
                actual_fuel_taken = car['actual_fuel_taken'] or 0
                variance_litre = round(monthly_litre - actual_fuel_taken, 2)
                variance_penalty = round(variance_litre * CURRENT_FUEL_RATE, 2)

            backup = car['backup_total'] if ('backup_total' in car and car['backup_total'] is not None) else 0

            # Calculate final net payouts
            # if base_kms == 0:
            #     # Payout depends strictly on the combined route cost flat block minus backup and advances
            #     captain_payment = round(calculated_route_cost - backup - (car['advance_amount'] or 0), 2)
            # else:
            #     captain_payment = round(display_rental + (monthly_litre * CURRENT_FUEL_RATE) - backup - variance_penalty - (car['advance_amount'] or 0), 2)

            if is_fixed_route:
                if fuel_diff < 0:
                    captain_payment = round(calculated_route_cost - backup - (car['advance_amount'] or 0) + fuel_diff_amount, 2)
                else:
                    captain_payment = round(calculated_route_cost - backup - (car['advance_amount'] or 0), 2)
            else:
                if fuel_diff_amount > 0:
                    captain_payment = round(display_rental + monthly_fuel_amount - backup + fuel_diff_amount - (car['advance_amount'] or 0), 2)
                else:
                    captain_payment = round(display_rental + monthly_fuel_amount - backup - abs(fuel_diff_amount) - (car['advance_amount'] or 0), 2)

            report_row = {
                'registration_no': car['registration_no'],
                'driver_code': car['driver_code'],
                'captain_name': car['captain_name'],
                'company_name': comp,
                'route_name': r_name if r_name else 'Unassigned',
                'contact1': car['contact1'],
                'contact2': car['contact2'],
                'car_model': car['car_model'],
                'ac_status': car['ac_status'],

                # Dynamic visual displays mapping instructions applied perfectly:
                'rental': display_rental,                # Displays 0 if Fixed Checkbox was active
                'route_cost': calculated_route_cost,    # Contains total base amount if Fixed

                'kms': base_kms,
                'working_days': wd,
                'fuel_cons': r_data['fuel_cons_per_km'],
                'fuelinlitre': fuelinlitre,
                'monthly_litre': monthly_litre,
                'monthly_fuel_amount': round(monthly_litre * CURRENT_FUEL_RATE, 2) if base_kms > 0 else 0.0,
                'backup': backup,
                'actual_fuel': actual_fuel_taken,
                'fuel_diff': variance_litre,
                'fuel_diff_amount': variance_penalty,
                'advance': car['advance_amount'] or 0,
                'day_off_reason': car['day_off_reason'] or 'Active',
                'captain_payment': captain_payment,
                'is_disabled': int(car['is_disabled']) if car.get('is_disabled') else 0
            }

            if filter_route and filter_route.lower() != report_row['route_name'].lower():
                continue
            if filter_route and filter_route.lower() != report_row['route_name'].lower():
                continue

            # 2. ✅ FIXED: Match against your actual database text strings
            if filter_attendance:
                current_reason = report_row['day_off_reason'].lower()
                target_filter = filter_attendance.lower()

                if target_filter == 'active':
                    # If looking for Active, skip rows that have medical issues or breakdowns
                    if 'active' not in current_reason:
                        continue
                elif target_filter == 'day_off':
                    # If looking for "Day Off", show rows matching either medical or breakdown strings
                    if "medical condition" not in current_reason and "car breakdown" not in current_reason:
                        continue


            if search_query:
                q = search_query.lower()
                if q not in report_row['registration_no'].lower() and \
                   q not in report_row['captain_name'].lower() and \
                   q not in report_row['driver_code'].lower() and \
                   q not in report_row['car_model'].lower():
                    continue

            compiled_master_ledger.append(report_row)
    return compiled_master_ledger

@app.route('/admin/edit_routes', methods=['GET', 'POST'])
def edit_routes():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    selected_route = None
    routes_list = []
    found_company = None
    all_routes = []

    # Merge the canonical 'routes' table with distinct 'cars.route_name'
    # references, the same way view_routes does, so the search box and
    # the index table surface every route the fleet actually uses.
    merged = {}  # key = (company, lowercase route_name) -> dict

    def _merge(comp, route_name, kms=None, fuel_cons=None, working_days=None,
               route_rental=None, in_routes_table=False, car_count=0):
        key = (comp, (route_name or '').strip().lower())
        if not key[1]:
            return
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                'route_name': route_name,
                'company_name': comp,
                'kms': kms,
                'fuel_cons_per_km': fuel_cons,
                'working_days': working_days,
                'route_rental': route_rental,
                'in_routes_table': in_routes_table,
                'car_count': car_count,
            }
        else:
            if existing['kms'] is None and kms is not None: existing['kms'] = kms
            if existing['fuel_cons_per_km'] is None and fuel_cons is not None: existing['fuel_cons_per_km'] = fuel_cons
            if existing['working_days'] is None and working_days is not None: existing['working_days'] = working_days
            if existing['route_rental'] is None and route_rental is not None: existing['route_rental'] = route_rental
            if in_routes_table: existing['in_routes_table'] = True
            existing['car_count'] += car_count

    for comp in COMPANY_MAP.keys():
        db_p = get_db_path(comp)
        conn = sqlite3.connect(db_p)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM routes")
            for row in cursor.fetchall():
                _merge(comp, row['route_name'],
                       kms=row['kms'], fuel_cons=row['fuel_cons_per_km'],
                       working_days=row['working_days'],
                       route_rental=row['route_rental'],
                       in_routes_table=True)
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "SELECT route_name, COUNT(*) FROM cars "
                "WHERE route_name IS NOT NULL AND TRIM(route_name) != '' "
                "AND route_name NOT IN ('-', '—') "
                "GROUP BY LOWER(route_name)"
            )
            for rname, cnt in cursor.fetchall():
                _merge(comp, rname, car_count=cnt)
        except sqlite3.OperationalError:
            pass
        conn.close()

    all_routes = sorted(merged.values(), key=lambda x: (x['company_name'], (x['route_name'] or '').lower()))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'search':
            search_target = (request.form.get('search_target', '') or '').strip()
            if not search_target or search_target in ('-', '—'):
                flash("Please enter a valid route name to search for.")
            else:
                # Case-insensitive lookup across every company partition
                for comp in COMPANY_MAP.keys():
                    db_p = get_db_path(comp)
                    conn = sqlite3.connect(db_p)
                    cursor = conn.cursor()
                    try:
                        cursor.execute(
                            "SELECT route_name, kms, working_days, route_rental, company_name, fuel_cons_per_km "
                            "FROM routes WHERE LOWER(route_name)=LOWER(?)",
                            (search_target,),
                        )
                        selected_route = cursor.fetchone()
                    except sqlite3.OperationalError:
                        selected_route = None
                    if selected_route:
                        found_company = comp
                        cursor.execute("SELECT route_name FROM routes")
                        routes_list = [r[0] for r in cursor.fetchall()]
                        conn.close()
                        break
                    conn.close()

                if not selected_route:
                    flash("No matching route profile discovered within any corporate partitions.")

        elif action == 'update_route':
            supplied = request.form.get('verify_password', '')
            if supplied != DESTRUCTIVE_PASSWORD:
                flash("❌ ACCESS DENIED: Invalid management passphrase. Route edit aborted.", "error")
                return redirect(url_for('edit_routes'))

            target_route = (request.form.get('target_route_name') or '').strip()
            comp_context = (request.form.get('comp_context') or '').strip()

            if not comp_context or comp_context in ('-', '—') or comp_context not in COMPANY_MAP:
                flash("❌ Cannot determine the corporate context for this route. Aborted.", "error")
                return redirect(url_for('edit_routes'))
            if not target_route or target_route in ('-', '—'):
                flash("❌ Cannot determine the route name. Aborted.", "error")
                return redirect(url_for('edit_routes'))

            kms = float(request.form.get('kms', 0))
            days = int(request.form.get('working_days', 0))
            rental = float(request.form.get('route_rental', 0))
            is_fixed = 1 if request.form.get('is_fixed_route') == '1' else 0
            cons = float(request.form.get('fuel_cons_per_km', 1))
            derived_litres = round(kms / cons, 2) if cons > 0 else 0

            db_p = get_db_path(comp_context)
            conn = sqlite3.connect(db_p)
            cursor = conn.cursor()

            # If the route isn't in the 'routes' table yet but is referenced by
            # cars, this is a chance to formally register it. We only update
            # existing rows here — adding a brand-new route from a car reference
            # is intentionally left to the dedicated Add Route workflow so we
            # never silently materialize rows from car-typed strings.
            cursor.execute(
                "SELECT route_name FROM routes WHERE LOWER(route_name)=LOWER(?)",
                (target_route,),
            )
            existing = cursor.fetchone()
            if not existing:
                conn.close()
                flash(
                    f"⚠️ Route '{target_route}' is not formally registered in {comp_context}. "
                    f"Use the 'Add Route' workflow to create it before editing.",
                    "error",
                )
                return redirect(url_for('edit_routes'))

            cursor.execute("""
                UPDATE routes SET kms=?, working_days=?, route_rental=?, fuel_cons_per_km=?, fuel_in_litres=?, is_fixed=?
                WHERE LOWER(route_name)=LOWER(?)
            """, (kms, days, rental, cons, derived_litres, is_fixed,target_route))
            is_fixed = request.form.get('is_fixed_route') == '1'
            kms = 0.0 if is_fixed else float(request.form.get('kms', 0))
            conn.commit()
            conn.close()
            flash("Route baseline configurations successfully calibrated!")

    return render_template(
        'edit_routes.html',
        route=selected_route,
        routes=routes_list,
        comp_context=found_company,
        all_routes=all_routes,
    )


@app.route('/admin/analytics', methods=['GET', 'POST'])
def fleet_analytics():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    if request.method == 'POST':
        action = request.form.get('action')
        reg = request.form.get('registration_no')
        comp_context = request.form.get('company_name')

        if action in ('disable_car', 'enable_car') and reg and comp_context in COMPANY_MAP:
            # Server-side password verification before toggling disabled state
            supplied = request.form.get('verify_password', '')
            if supplied != DESTRUCTIVE_PASSWORD:
                flash("❌ ACCESS DENIED: Invalid management passphrase. Vehicle status change aborted.", "error")
                return redirect(url_for('fleet_analytics'))

            new_state = 1 if action == 'disable_car' else 0
            db_p = get_db_path(comp_context)
            conn = sqlite3.connect(db_p)
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE cars SET is_disabled=? WHERE registration_no=?", (new_state, reg))
                cursor.execute("""
                    INSERT INTO transaction_logs (registration_no, timestamp, entry_type, description)
                    VALUES (?, ?, 'Admin Correction', ?)
                """, (reg, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      f"Vehicle {'DISABLED' if new_state else 'RE-ENABLED'} by admin. Historical data preserved."))
                conn.commit()
                verb = "DISABLED" if new_state else "RE-ENABLED"
                flash(f"🔒 Vehicle '{reg}' has been {verb} in {comp_context}. Historical data retained.", "success")
            except Exception as e:
                flash(f"❌ Status change failed: {e}", "error")
            conn.close()
            return redirect(url_for('fleet_analytics'))

    filter_company = request.args.get('company', '').strip()
    filter_route = request.args.get('route', '').strip()
    search_query = request.args.get('search_query', '').strip()
    filter_att = request.args.get('attendance_state', '').strip()

    ledger = generate_master_ledger(filter_company, filter_route, search_query, filter_att)
    return render_template('analytics.html', ledger=ledger, companies=COMPANY_MAP.keys(), select_comp=filter_company, select_route=filter_route, query=search_query)


# --- INDIVIDUAL PROFILE SEARCH ROUTE LINK ---
@app.route('/admin/user_profile/<company>/<registration_no>')
def user_profile(company, registration_no):
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))
    if company not in COMPANY_MAP:
        flash("Scope execution mismatch: Invalid company token.")
        return redirect(url_for('fleet_analytics'))

    db_p = get_db_path(company)
    conn = sqlite3.connect(db_p)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cars WHERE registration_no=?", (registration_no,))
    car_row = cursor.fetchone()
    if not car_row:
        conn.close()
        flash("Target vehicle statement dossier could not be mapped.")
        return redirect(url_for('fleet_analytics'))

    car = dict(car_row)
    cursor.execute("SELECT * FROM routes WHERE route_name=?", (car['route_name'],))
    route_row = cursor.fetchone()

    backup = car['backup_total'] if ('backup_total' in car and car['backup_total'] is not None) else 0

    cursor.execute("SELECT * FROM transaction_logs WHERE registration_no=? ORDER BY id DESC", (registration_no,))
    logs = [dict(log) for log in cursor.fetchall()]
    conn.close()

    r_data = dict(route_row) if route_row else {'company_name': company, 'kms':0.0, 'fuel_cons_per_km':1.0, 'working_days':0, 'route_rental':0.0, 'is_fixed':0}
    is_fixed_route = r_data.get('is_fixed', 0) == 1

    kms = car['custom_kms'] if car['custom_kms'] is not None else r_data['kms']
    wd = car['custom_working_days'] if car['custom_working_days'] is not None else r_data['working_days']
    base_rental = car['custom_rental'] if car['custom_rental'] is not None else r_data['route_rental']

    if is_fixed_route:
        display_rental = 0.0
        fuelinlitre = 0.0
        monthly_litre = 0.0
        monthly_fuel_amount = 0.0
        fuel_diff = 0.0
        fuel_diff_amount = 0.0
        calculated_route_cost = base_rental
        captain_payment = round(calculated_route_cost - backup - (car['advance_amount'] or 0), 2)
    else:
        display_rental = base_rental
        fuelinlitre = round(kms / r_data['fuel_cons_per_km'], 2) if r_data['fuel_cons_per_km'] > 0 else 0
        monthly_litre = round(wd * fuelinlitre // 10, 2)
        monthly_fuel_amount = round(monthly_litre * CURRENT_FUEL_RATE, 2)
        fuel_diff = round(monthly_litre - (car['actual_fuel_taken'] or 0), 2)
        fuel_diff_amount = round(fuel_diff * CURRENT_FUEL_RATE, 2)
        calculated_route_cost = round(display_rental + monthly_fuel_amount, 2)
        if fuel_diff_amount > 0:
            captain_payment = round(display_rental + monthly_fuel_amount - backup + fuel_diff_amount - (car['advance_amount'] or 0), 2)
        else:
            captain_payment = round(display_rental + monthly_fuel_amount - backup - abs(fuel_diff_amount) - (car['advance_amount'] or 0), 2)

    telemetry = {
        'profile': car,
        'company': company,
        'kms': kms, 'working_days': wd, 'rental': display_rental, 'route_cost': calculated_route_cost,
        'fuelinlitre': fuelinlitre, 'monthly_litre': monthly_litre,
        'monthly_fuel_amount': monthly_fuel_amount, 'fuel_diff': fuel_diff,
        'fuel_diff_amount': fuel_diff_amount, 'captain_payment': captain_payment,
        'rate': CURRENT_FUEL_RATE
    }
    return render_template('user_profile.html', data=telemetry, logs=logs)


@app.route('/admin/daily_update', methods=['GET', 'POST'])
def daily_update():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))

    all_drivers = []
    for comp in COMPANY_MAP.keys():
        db_p = get_db_path(comp)
        conn = sqlite3.connect(db_p)
        cursor = conn.cursor()
        cursor.execute("SELECT registration_no, captain_name, day_off_reason, is_disabled FROM cars")
        for row in cursor.fetchall():
            if row[3]:
                continue  # skip disabled cars in the daily update picker
            all_drivers.append({'reg': row[0], 'name': row[1], 'status': row[2] if row[2] else 'Active', 'company': comp})
        conn.close()

    if request.method == 'POST':
        target_compound = request.form.get('target_driver')
        backup_input = request.form.get('backup_amount')
        fuel_input = request.form.get('today_fuel', '0')

        if not target_compound:
            flash("❌ Please select a valid target vehicle profile.", "error")
            return redirect(url_for('daily_update'))

        reg, comp_context = target_compound.split('|')
        backup_amt = float(backup_input) if backup_input else 0.0
        fuel_amt = float(fuel_input) if fuel_input else 0.0

        # ✨ DYNAMIC TARGET DATE LOGIC CHANGE DETECTOR
        if 'selected_date' in session:
            # Inject custom target date alongside current clock hours/minutes/seconds
            now_str = f"{session['selected_date']} {datetime.now().strftime('%H:%M:%S')}"
        else:
            # Fallback default configuration: real-time system execution clock
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db_p = get_db_path(comp_context)
        conn = sqlite3.connect(db_p)
        cursor = conn.cursor()

        try:
            # Refuse to log updates against a disabled car; the admin must re-enable it first.
            cursor.execute("SELECT is_disabled FROM cars WHERE registration_no=?", (reg,))
            state = cursor.fetchone()
            if state and state[0]:
                flash(f"⚠️ Vehicle [{reg}] is currently DISABLED. Re-enable it from Analytics before logging daily updates.", "error")
                conn.close()
                return redirect(url_for('daily_update'))

            cursor.execute("""
                UPDATE cars
                    SET backup_total = IFNULL(backup_total, 0) + ?,
                        actual_fuel_taken = IFNULL(actual_fuel_taken, 0) + ?
                WHERE registration_no = ?
            """, (backup_amt, fuel_amt, reg))

            cursor.execute("""
                INSERT INTO transaction_logs (registration_no, timestamp, entry_type, fuel_added, backup_added, description)
                VALUES (?, ?, 'Daily Update', ?, ?, ?)
            """, (reg, now_str, fuel_amt, backup_amt, f"Logged Entry: Fuel + {fuel_amt}L, Backup Fines + Rs.{backup_amt}"))

            conn.commit()
            flash(f"🎯 Successfully processed logs for [{reg}]: Added Rs.{backup_amt} Backup Fine & {fuel_amt} Liters of fuel.", "success")
        except Exception as e:
            flash(f"❌ Database Transaction Error: {e}", "error")
        conn.close()
        return redirect(url_for('daily_update'))

    return render_template('daily_update.html', all_drivers=all_drivers)

@app.route('/admin/download_csv')
def download_csv():
    if 'admin_logged_in' not in session: return redirect(url_for('admin_portal'))
    filter_company = request.args.get('company', '').strip()
    filter_route = request.args.get('route', '').strip()
    search_query = request.args.get('search_query', '').strip()

    ledger = generate_master_ledger(filter_company, filter_route, search_query)

    def generate():
        data = io.StringIO()
        writer = csv.writer(data)
        writer.writerow([
            'System Driver Code', 'Vehicle Registration', 'Company DB Instance', 'Captain Name',
            'Primary Phone', 'Secondary Phone', 'Car Model', 'A/C Status', 'Assigned Route',
            'Base Rental (Rs)', 'Distance KMs', 'Active Working Days', 'Trip Target Ltrs',
            'Monthly Target Ltrs', 'Calculated Monthly Fuel Cost', 'Backup Leave Fine',
            'Actual Taken Ltrs', 'Variance Ltrs', 'Variance Penalty Cost', 'Disbursed Advance',
            'Current Status Profile', 'Final Net Payable to Captain'
        ])
        yield data.getvalue()
        data.seek(0)
        data.truncate(0)

        for r in ledger:
            writer.writerow([
                r['driver_code'], r['registration_no'], r['company_name'], r['captain_name'],
                r['contact1'], r['contact2'], r['car_model'], r['ac_status'], r['route_name'],
                r['rental'], r['kms'], r['working_days'], r['fuelinlitre'],
                r['monthly_litre'], r['monthly_fuel_amount'], r['backup'],
                r['actual_fuel'], r['fuel_diff'], r['fuel_diff_amount'], r['advance'],
                r['day_off_reason'], r['captain_payment']
            ])
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)

    response = Response(generate(), mimetype='text/csv')
    response.headers.set("Content-Disposition", "attachment", filename=f"Fleet_Master_Ledger_{datetime.now().strftime('%Y%m%d')}.csv")
    return response


# --- TRANSACTION LOG REPORT GENERATOR ---
def query_transaction_logs(filter_company, filter_reg, filter_entry_type, date_from, date_to, search_query):
    """Pull transaction logs across all company DBs that match the active filters.
    Timestamps are stored as 'YYYY-MM-DD HH:MM:SS' so lexicographic compares on
    the prefix work for date-only bounds.
    """
    compiled = []
    for comp in COMPANY_MAP.keys():
        if filter_company and filter_company != comp:
            continue
        db_p = get_db_path(comp)
        conn = sqlite3.connect(db_p)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT registration_no, captain_name, route_name FROM cars")
            car_meta = {row['registration_no']: dict(row) for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            car_meta = {}

        try:
            cursor.execute("SELECT * FROM transaction_logs")
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            rows = []
        conn.close()

        for r in rows:
            ts = r['timestamp'] or ''
            if date_from and ts[:10] < date_from:
                continue
            if date_to and ts[:10] > date_to:
                continue
            if filter_reg and filter_reg.lower() != (r['registration_no'] or '').lower():
                continue
            if filter_entry_type and filter_entry_type != r['entry_type']:
                continue
            if search_query:
                q = search_query.lower()
                if q not in (r['registration_no'] or '').lower() and \
                   q not in (r['description'] or '').lower():
                    continue

            meta = car_meta.get(r['registration_no'], {})
            compiled.append({
                'id': r['id'],
                'timestamp': ts,
                'company_name': comp,
                'registration_no': r['registration_no'],
                'captain_name': meta.get('captain_name', '—'),
                'route_name': meta.get('route_name', '—'),
                'entry_type': r['entry_type'],
                'fuel_added': r['fuel_added'] or 0,
                'backup_added': r['backup_added'] or 0,
                'advance_added': r['advance_added'] or 0,
                'description': r['description'] or ''
            })

    compiled.sort(key=lambda x: x['timestamp'], reverse=True)
    return compiled


@app.route('/admin/reports', methods=['GET', 'POST'])
def transaction_reports():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_portal'))

    # ✨ NEW: HANDLE LOG EDIT & DELETE ACTIONS
    if request.method == 'POST':
        action = request.form.get('action')
        # supplied_password = request.form.get('verify_password', '')

        # if supplied_password != DESTRUCTIVE_PASSWORD:
        #     flash("❌ ACCESS DENIED: Invalid management passphrase. Operation aborted.", "error")
        #     return redirect(url_for('transaction_reports', **request.args))

        log_id = request.form.get('log_id')
        comp_context = request.form.get('company_name')

        if comp_context in COMPANY_MAP and log_id:
            db_p = get_db_path(comp_context)
            conn = sqlite3.connect(db_p)
            cursor = conn.cursor()

            try:
                if action == 'delete_log':
                    # Before purging, we read metrics to safely reverse the adjustments from the car record if required
                    cursor.execute("SELECT registration_no, fuel_added, backup_added, advance_added FROM transaction_logs WHERE id=?", (log_id,))
                    log_row = cursor.fetchone()
                    if log_row:
                        reg_no, f_add, b_add, a_add = log_row
                        # Reverse values from the current car balance totals
                        cursor.execute("""
                            UPDATE cars
                            SET actual_fuel_taken = MAX(0, IFNULL(actual_fuel_taken, 0) - ?),
                                backup_total = MAX(0, IFNULL(backup_total, 0) - ?),
                                advance_amount = MAX(0, IFNULL(advance_amount, 0) - ?)
                            WHERE registration_no = ?
                        """, (f_add or 0, b_add or 0, a_add or 0, reg_no))

                    cursor.execute("DELETE FROM transaction_logs WHERE id=?", (log_id,))
                    conn.commit()
                    flash(f"🗑️ Transaction Log ID #{log_id} was successfully deleted and balances reversed.", "success")

                elif action == 'edit_log':
                    new_desc = request.form.get('description', '').strip()
                    new_fuel = float(request.form.get('fuel_added', 0))
                    new_backup = float(request.form.get('backup_added', 0))
                    new_advance = float(request.form.get('advance_added', 0))

                    # Read original log to calculate structural variance shifts
                    cursor.execute("SELECT registration_no, fuel_added, backup_added, advance_added FROM transaction_logs WHERE id=?", (log_id,))
                    log_row = cursor.fetchone()
                    if log_row:
                        reg_no, old_f, old_b, old_a = log_row
                        diff_f = new_fuel - (old_f or 0)
                        diff_b = new_backup - (old_b or 0)
                        diff_a = new_advance - (old_a or 0)

                        # Apply adjustments difference on top of car master values
                        cursor.execute("""
                            UPDATE cars
                            SET actual_fuel_taken = IFNULL(actual_fuel_taken, 0) + ?,
                                backup_total = IFNULL(backup_total, 0) + ?,
                                advance_amount = IFNULL(advance_amount, 0) + ?
                            WHERE registration_no = ?
                        """, (diff_f, diff_b, diff_a, reg_no))

                    cursor.execute("""
                        UPDATE transaction_logs
                        SET fuel_added=?, backup_added=?, advance_added=?, description=?
                        WHERE id=?
                    """, (new_fuel, new_backup, new_advance, new_desc, log_id))
                    conn.commit()
                    flash(f"✏️ Transaction Log ID #{log_id} successfully updated.", "success")

            except Exception as e:
                flash(f"❌ Database Transaction Error: {e}", "error")
            finally:
                conn.close()

        return redirect(url_for('transaction_reports', **request.args))

    # Keep your existing GET data filtering pipeline below untouched
    filter_company = request.args.get('company', '').strip()
    filter_reg = request.args.get('registration_no', '').strip()
    filter_entry_type = request.args.get('entry_type', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    search_query = request.args.get('search_query', '').strip()

    logs = query_transaction_logs(filter_company, filter_reg, filter_entry_type, date_from, date_to, search_query)

    # ... Rest of your summary rollup data metrics & return render_template statement block ...

    # Summary rollups for the header strip
    total_fuel = sum(l['fuel_added'] for l in logs)
    total_backup = sum(l['backup_added'] for l in logs)
    total_advance = sum(l['advance_added'] for l in logs)

    # Distinct list of registration nos for the autocomplete-ish dropdown
    reg_options = sorted({l['registration_no'] for l in logs if l['registration_no']})

    return render_template('reports.html',
                           logs=logs,
                           companies=COMPANY_MAP.keys(),
                           select_comp=filter_company,
                           select_reg=filter_reg,
                           select_entry_type=filter_entry_type,
                           date_from=date_from,
                           date_to=date_to,
                           query=search_query,
                           reg_options=reg_options,
                           entry_types=['Daily Update', 'Advance Request', 'Attendance Change', 'Admin Correction'],
                           total_fuel=round(total_fuel, 2),
                           total_backup=round(total_backup, 2),
                           total_advance=round(total_advance, 2),
                           total_rows=len(logs))


@app.route('/admin/reports/download_csv')
def download_reports_csv():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_portal'))

    filter_company = request.args.get('company', '').strip()
    filter_reg = request.args.get('registration_no', '').strip()
    filter_entry_type = request.args.get('entry_type', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    search_query = request.args.get('search_query', '').strip()

    logs = query_transaction_logs(filter_company, filter_reg, filter_entry_type, date_from, date_to, search_query)

    def generate():
        data = io.StringIO()
        writer = csv.writer(data)
        writer.writerow(['Log ID', 'Timestamp', 'Company', 'Registration No', 'Captain',
                         'Route', 'Entry Type', 'Fuel Added (Ltr)', 'Backup Added (Rs)',
                         'Advance Added (Rs)', 'Description'])
        yield data.getvalue()
        data.seek(0); data.truncate(0)

        for l in logs:
            writer.writerow([l['id'], l['timestamp'], l['company_name'], l['registration_no'],
                             l['captain_name'], l['route_name'], l['entry_type'],
                             l['fuel_added'], l['backup_added'], l['advance_added'],
                             l['description']])
            yield data.getvalue()
            data.seek(0); data.truncate(0)

    filename = f"Transaction_Logs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    response = Response(generate(), mimetype='text/csv')
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    return response


@app.route('/admin/set_session_date', methods=['POST'])
def set_session_date():
    if 'admin_logged_in' not in session:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    date_type = request.form.get('date_type')

    if date_type == 'today':
        session.pop('selected_date', None) # Clear out session variable to fall back to machine real-time clock
        return jsonify({'status': 'success', 'date': 'today'})

    elif date_type == 'custom':
        custom_date = request.form.get('custom_date_value', '').strip()
        if not custom_date:
            return jsonify({'status': 'error', 'message': 'No date string was selected.'})

        session['selected_date'] = custom_date # Store selected clean ISO string format string (YYYY-MM-DD)
        return jsonify({'status': 'success', 'date': custom_date})

    return jsonify({'status': 'error', 'message': 'Invalid action parameters payload.'})

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_verified_for_edit', None)
    return redirect(url_for('admin_portal'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('user_company', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)