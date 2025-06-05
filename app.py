# app.py
import sqlite3
import os
import sys
import datetime
from dateutil.relativedelta import relativedelta
from flask import (
    Flask, render_template, request, g, redirect, url_for, flash, jsonify
)

# --- Configuration ---
DATABASE = 'inventory.db'
ALLOWED_ITEM_STATUSES = ['Available', 'Reserved', 'Installed', 'Broken', 'Returned']
PART_TYPES_CATEGORIES = ["Screen", "Battery", "Back Cover", "Charging Port", "Camera", "Adhesive", "Small Parts", "Tools", "Other"]
OLD_STOCK_THRESHOLD_MONTHS = 5
ALLOWED_BOOKING_STATUSES = ['Booked In', 'In Progress', 'Awaiting Part', 'Ready for Collection', 'Completed', 'Cancelled']
DB_SCHEMA_REQ = 8 # Required schema version for this app


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a_very_secret_dev_key_change_me')

# --- Database Connection Handling ---
def get_db():
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(DATABASE)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON") # Ensure foreign key constraints are enabled
        except sqlite3.Error as e:
            print(f"DB CONNECT ERROR: {e}", file=sys.stderr)
            g.db = None # Make sure g.db is None if connection fails
            raise # Re-raise the exception to signal a problem
    return g.db

@app.teardown_appcontext
def close_db(error): # error argument is passed by Flask
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except sqlite3.Error as e: # Catch potential errors during close
            print(f"ERROR CLOSING DB: {e}", file=sys.stderr)
    if error: # Log Flask teardown errors if any
        print(f"Request teardown error: {error}", file=sys.stderr)

# --- Utility ---
def flash_errors(errors):
    for error_message in errors: # Renamed e to error_message for clarity
        flash(error_message, 'error')

# --- Routes ---

@app.route('/')
def index():
    part_type_summary = []
    show_old_stock_alert = False
    filter_brand = request.args.get('brand', '').strip()
    filter_model = request.args.get('model', '').strip()
    filter_type = request.args.get('type', '').strip()
    search_term_parts = request.args.get('search_term_parts', '').strip()

    brands, models, part_type_categories_for_filter = [], [], []

    try:
        conn = get_db()
        cursor = conn.cursor()

        threshold_date_str = (datetime.datetime.now() - relativedelta(months=OLD_STOCK_THRESHOLD_MONTHS)).strftime('%Y-%m-%d %H:%M:%S')
        alert_query = """
            SELECT 1 FROM stock_orders so
            WHERE so.order_date < ? AND EXISTS (
                SELECT 1 FROM stock_order_lines sol
                JOIN inventory_items i ON sol.id = i.stock_order_line_id
                WHERE sol.stock_order_id = so.id AND i.status IN ('Available', 'Reserved')
            ) LIMIT 1;
        """
        cursor.execute(alert_query, (threshold_date_str,))
        if cursor.fetchone():
            show_old_stock_alert = True

        cursor.execute("SELECT DISTINCT brand FROM part_types WHERE brand IS NOT NULL AND brand != '' ORDER BY brand")
        brands = [row['brand'] for row in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT model FROM part_types WHERE model IS NOT NULL AND model != '' ORDER BY model")
        models = [row['model'] for row in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT part_type FROM part_types WHERE part_type IS NOT NULL AND part_type != '' ORDER BY part_type")
        part_type_categories_for_filter = [row['part_type'] for row in cursor.fetchall()]

        query_base = """
            SELECT
                pt.id, pt.part_name, pt.part_number, pt.artikelnummer, pt.brand, pt.model, pt.part_type, pt.storage_location,
                COUNT(i.id) AS total_stock,
                SUM(CASE WHEN i.status = 'Available' THEN 1 ELSE 0 END) AS available_stock,
                SUM(CASE WHEN i.status = 'Reserved' THEN 1 ELSE 0 END) AS reserved_stock,
                SUM(CASE WHEN i.status = 'Broken' THEN 1 ELSE 0 END) AS broken_stock,
                SUM(CASE WHEN i.status = 'Returned' THEN 1 ELSE 0 END) AS returned_stock
            FROM part_types pt
            LEFT JOIN inventory_items i ON pt.id = i.part_type_id
        """

        conditions = []
        params = []

        if filter_brand:
            conditions.append("LOWER(pt.brand) LIKE LOWER(?)")
            params.append(f"%{filter_brand}%")
        if filter_model:
            conditions.append("LOWER(pt.model) LIKE LOWER(?)")
            params.append(f"%{filter_model}%")
        if filter_type:
            conditions.append("pt.part_type = ?")
            params.append(filter_type)

        if search_term_parts:
            search_like_term = f"%{search_term_parts}%"
            conditions.append("(LOWER(IFNULL(pt.artikelnummer,'')) LIKE LOWER(?) OR LOWER(IFNULL(pt.part_number,'')) LIKE LOWER(?))")
            params.extend([search_like_term, search_like_term])

        if conditions:
            query_base += " WHERE " + " AND ".join(conditions)

        query_base += """
            GROUP BY pt.id, pt.part_name, pt.part_number, pt.artikelnummer, pt.brand, pt.model, pt.part_type, pt.storage_location
            ORDER BY
                (CASE WHEN SUM(CASE WHEN i.status = 'Broken' THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END) ASC,
                SUM(CASE WHEN i.status = 'Broken' THEN 1 ELSE 0 END) DESC,
                (CASE WHEN SUM(CASE WHEN i.status = 'Available' THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END) ASC,
                SUM(CASE WHEN i.status = 'Available' THEN 1 ELSE 0 END) DESC,
                (CASE WHEN SUM(CASE WHEN i.status = 'Reserved' THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END) ASC,
                SUM(CASE WHEN i.status = 'Reserved' THEN 1 ELSE 0 END) DESC,
                (CASE WHEN SUM(CASE WHEN i.status = 'Returned' THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END) ASC,
                SUM(CASE WHEN i.status = 'Returned' THEN 1 ELSE 0 END) DESC,
                COUNT(i.id) DESC,
                pt.brand ASC,
                pt.model ASC,
                pt.part_name ASC;
        """
        cursor.execute(query_base, params)
        part_type_summary = cursor.fetchall()

    except sqlite3.Error as e:
        print(f"DB Error index: {e}", file=sys.stderr)
        flash(f"Error retrieving part type summary: {e}", "error")
    except Exception as e:
        print(f"Error index: {e}", file=sys.stderr)
        flash("An unexpected error occurred while loading the main page.", "error")

    current_filters = {
        'brand': filter_brand,
        'model': filter_model,
        'type': filter_type,
        'search_term_parts': search_term_parts
    }

    return render_template('index.html',
                           part_type_summary=part_type_summary,
                           brands_filter=brands,
                           models_filter=models,
                           part_type_categories_filter=part_type_categories_for_filter,
                           current_filters=current_filters,
                           show_old_stock_alert=show_old_stock_alert,
                           OLD_STOCK_THRESHOLD_MONTHS=OLD_STOCK_THRESHOLD_MONTHS)

@app.route('/part_type/<int:part_type_id>/details')
def part_type_details(part_type_id):
    part_type_info = None
    items_processed = []

    # Get filter parameters from request.args
    search_stock_order = request.args.get('search_stock_order', '').strip()
    search_gpc = request.args.get('search_gpc', '').strip()
    search_booking_id_str = request.args.get('search_booking_id', '').strip()
    search_date = request.args.get('search_date', '').strip() # YYYY-MM-DD format

    # Store current filters for template repopulation
    current_filters = {
        'search_stock_order': search_stock_order,
        'search_gpc': search_gpc,
        'search_booking_id': search_booking_id_str,
        'search_date': search_date
    }


    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM part_types WHERE id = ?", (part_type_id,))
        part_type_info = cursor.fetchone()
        if not part_type_info: flash(f"Part Type ID {part_type_id} not found.", "error"); return redirect(url_for('index'))
        query = """
            SELECT i.id AS item_id, i.serial_number, i.status AS item_status, i.notes AS item_notes,
                   i.date_received, i.last_updated, so.order_number AS stock_order_number,
                   b.id AS booking_id, b.customer_name AS booking_customer, b.gpc_number AS booking_gpc_number,
                   pt_parent.storage_location AS default_storage_location
            FROM inventory_items i
            LEFT JOIN stock_order_lines sol ON i.stock_order_line_id = sol.id
            LEFT JOIN stock_orders so ON sol.stock_order_id = so.id
            LEFT JOIN booking_parts_used bpu ON i.id = bpu.inventory_item_id
            LEFT JOIN bookings b ON bpu.booking_id = b.id
            LEFT JOIN part_types pt_parent ON i.part_type_id = pt_parent.id
            WHERE i.part_type_id = ?
        """
        params = [part_type_id]
        conditions = []

        if search_stock_order:
            conditions.append("LOWER(IFNULL(so.order_number,'')) LIKE LOWER(?)")
            params.append(f"%{search_stock_order}%")
        
        if search_gpc:
            conditions.append("LOWER(IFNULL(b.gpc_number,'')) LIKE LOWER(?)")
            params.append(f"%{search_gpc}%")

        search_booking_id_int = None
        if search_booking_id_str:
            try:
                search_booking_id_int = int(search_booking_id_str)
                conditions.append("b.id = ?")
                params.append(search_booking_id_int)
            except ValueError:
                flash("Invalid Booking ID. Must be a number.", "error")

        if search_date:
            try:
                datetime.datetime.strptime(search_date, '%Y-%m-%d')
                conditions.append("SUBSTR(i.date_received, 1, 10) = ?")
                params.append(search_date)
            except ValueError:
                flash("Invalid Date format. Please use YYYY-MM-DD.", "error")

        if conditions:
            query += " AND " + " AND ".join(conditions)

        query += " ORDER BY i.status, i.id;"
        cursor.execute(query, tuple(params)); items_raw = cursor.fetchall()
        now_naive = datetime.datetime.now()
        for item_row in items_raw:
            item_dict = dict(item_row); days_in_stock = 0
            date_received_str = item_dict.get('date_received')
            if date_received_str:
                try:
                    date_received_dt_naive = datetime.datetime.strptime(date_received_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                    days_in_stock = (now_naive - date_received_dt_naive).days
                except (ValueError, TypeError) as e_date: print(f"Warning: Could not parse date_received '{date_received_str}' for item ID {item_dict.get('item_id')}: {e_date}", file=sys.stderr)
            item_dict['days_in_stock'] = days_in_stock; items_processed.append(item_dict)
    except sqlite3.Error as e: print(f"DB Error part_type_details: {e}", file=sys.stderr); flash(f"Error: {e}", "error"); return redirect(url_for('index'))
    except Exception as e: print(f"Error part_type_details: {e}", file=sys.stderr); flash("Unexpected error.", "error"); return redirect(url_for('index'))
    return render_template('part_type_details.html',
                           part_type_info=part_type_info,
                           items=items_processed,
                           allowed_item_statuses=ALLOWED_ITEM_STATUSES,
                           return_url=url_for('part_type_details', part_type_id=part_type_id, **current_filters), # Pass current filters to return_url
                           current_filters=current_filters) # Pass filters to template

@app.route('/inventory/item/<int:item_id>/status', methods=['POST'])
def update_item_status(item_id):
    new_status = request.form.get('new_status'); return_url = request.form.get('return_url', url_for('index'))
    if not new_status or new_status not in ALLOWED_ITEM_STATUSES: flash("Invalid status.", "error"); return redirect(return_url)
    conn = get_db()
    try:
        cursor = conn.cursor(); cursor.execute("UPDATE inventory_items SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?", (new_status, item_id))
        if cursor.rowcount == 0: flash(f"Item ID {item_id} not found.", "error")
        else: conn.commit(); flash(f"Status for item ID {item_id} updated to '{new_status}'.", "success")
    except sqlite3.Error as e:
        if conn: conn.rollback(); print(f"DB Error: {e}", file=sys.stderr); flash(f"DB error: {e}", "error")
    except Exception as e:
        if conn: conn.rollback(); print(f"Unexpected error: {e}", file=sys.stderr); flash(f"Error: {e}", "error")
    return redirect(return_url)

@app.route('/part_types/add', methods=['GET'])
def add_part_type_form():
    return render_template('add_part_type.html', part_types_categories=PART_TYPES_CATEGORIES, submitted_data={})

@app.route('/part_types/add', methods=['POST'])
def add_part_type():
    part_name = request.form.get('part_name', '').strip(); part_number = request.form.get('part_number', '').strip() or None
    artikelnummer = request.form.get('artikelnummer', '').strip() or None; part_type_category = request.form.get('part_type') or None
    brand = request.form.get('brand', '').strip() or None; model = request.form.get('model', '').strip() or None
    cost_price_str = request.form.get('cost_price', '').strip(); storage_location = request.form.get('storage_location', '').strip() or None
    description = request.form.get('description', '').strip() or None; errors = []
    if not part_name: errors.append("Part Name is required.")
    cost_price = None
    if cost_price_str:
        try:
            cost_price = float(cost_price_str)
            if cost_price < 0: errors.append("Cost price must be non-negative.")
        except ValueError: errors.append("Cost price must be a valid number.")
    if errors: flash_errors(errors); return render_template('add_part_type.html', part_types_categories=PART_TYPES_CATEGORIES, submitted_data=request.form), 400
    conn = get_db()
    try:
        cursor = conn.cursor()
        sql = "INSERT INTO part_types (part_name,part_number,artikelnummer,part_type,brand,model,cost_price,storage_location,description,created_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"
        values = (part_name,part_number,artikelnummer,part_type_category,brand,model,cost_price,storage_location,description)
        cursor.execute(sql, values); conn.commit()
        flash(f"Part Type '{part_name}' added!", 'success'); return redirect(url_for('part_types_overview'))
    except sqlite3.IntegrityError as e:
        conn.rollback(); err_msg = str(e).lower()
        if 'part_types.part_number' in err_msg : flash(f"Error: SKU '{part_number}' already exists.", 'error')
        elif 'part_types.artikelnummer' in err_msg: flash(f"Error: Artikelnummer '{artikelnummer}' already exists.", 'error')
        else: flash(f"DB integrity error: {e}", 'error')
    except sqlite3.Error as e:
        if conn: conn.rollback(); flash(f"DB error: {e}", 'error'); print(e, file=sys.stderr)
    except Exception as e:
        if conn: conn.rollback(); flash(f"Unexpected error: {e}", "error"); print(e, file=sys.stderr)
    return render_template('add_part_type.html', part_types_categories=PART_TYPES_CATEGORIES, submitted_data=request.form), 500

@app.route('/part_types/overview')
def part_types_overview():
    part_types_list = []
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id,part_name,part_number,artikelnummer,brand,model,part_type FROM part_types ORDER BY brand,model,part_name ASC")
        part_types_list = cursor.fetchall()
    except sqlite3.Error as e: print(f"DB Error: {e}", file=sys.stderr); flash(f"Error: {e}", "error")
    except Exception as e: print(f"Error: {e}", file=sys.stderr); flash("Unexpected error.", "error")
    return render_template('part_types_overview.html', part_types=part_types_list)

@app.route('/part_type/<int:part_type_id>/edit', methods=['GET'])
def edit_part_type_form(part_type_id):
    part_type_data = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id,part_name,part_number,artikelnummer,part_type,brand,model,cost_price,storage_location,description FROM part_types WHERE id=?", (part_type_id,))
        part_type_data = cursor.fetchone()
        if not part_type_data: flash(f"Part Type ID {part_type_id} not found.", "error"); return redirect(url_for('part_types_overview'))
    except sqlite3.Error as e: flash(f"Error loading details: {e}", "error"); return redirect(url_for('part_types_overview'))
    except Exception as e: flash(f"Unexpected error: {e}", "error"); return redirect(url_for('part_types_overview'))
    return render_template('edit_part_type.html', part_type=part_type_data, part_types_categories=PART_TYPES_CATEGORIES)

@app.route('/part_type/<int:part_type_id>/edit', methods=['POST'])
def update_part_type(part_type_id):
    conn = get_db(); current_part_type_db_vals = None
    try:
        cursor_check = conn.cursor()
        cursor_check.execute("SELECT part_number,artikelnummer FROM part_types WHERE id=?", (part_type_id,))
        current_part_type_db_vals = cursor_check.fetchone()
        if not current_part_type_db_vals: flash(f"Part Type ID {part_type_id} not found.", "error"); return redirect(url_for('part_types_overview'))
    except sqlite3.Error as e_fetch:
        flash(f"DB error fetching current part type: {e_fetch}", "error")
        form_data_repop = {k:v for k,v in request.form.items()}; form_data_repop['id'] = part_type_id
        return render_template('edit_part_type.html', part_type=form_data_repop, part_types_categories=PART_TYPES_CATEGORIES), 500
    part_name = request.form.get('part_name','').strip(); part_number = request.form.get('part_number','').strip() or None
    artikelnummer = request.form.get('artikelnummer','').strip() or None; part_type_category = request.form.get('part_type_category') or None
    brand = request.form.get('brand','').strip() or None; model = request.form.get('model','').strip() or None
    cost_price_str = request.form.get('cost_price','').strip(); storage_location = request.form.get('storage_location','').strip() or None
    description = request.form.get('description','').strip() or None; errors = []
    if not part_name: errors.append("Part Name required.")
    cost_price = None
    if cost_price_str:
        try:
            cost_price = float(cost_price_str)
            if cost_price < 0: errors.append("Cost price must be non-negative.")
        except ValueError: errors.append("Cost price must be a valid number.")
    if errors:
        flash_errors(errors); form_data_dict = {k:v for k,v in request.form.items()}; form_data_dict['id'] = part_type_id
        form_data_dict['part_type'] = part_type_category
        return render_template('edit_part_type.html', part_type=form_data_dict, part_types_categories=PART_TYPES_CATEGORIES), 400
    try:
        cursor = conn.cursor()
        sql = "UPDATE part_types SET part_name=?,part_number=?,artikelnummer=?,part_type=?,brand=?,model=?,cost_price=?,storage_location=?,description=? WHERE id=?"
        values = (part_name,part_number,artikelnummer,part_type_category,brand,model,cost_price,storage_location,description,part_type_id)
        cursor.execute(sql,values); conn.commit()
        flash(f"Part Type '{part_name}' updated!",'success'); return redirect(url_for('part_types_overview'))
    except sqlite3.IntegrityError as e:
        conn.rollback(); err_msg=str(e).lower()
        original_db_pn = current_part_type_db_vals['part_number'] if current_part_type_db_vals else None
        original_db_an = current_part_type_db_vals['artikelnummer'] if current_part_type_db_vals else None
        if 'part_types.part_number' in err_msg and (part_number != original_db_pn or (part_number and not original_db_pn)): flash(f"Error: Part Number '{part_number}' already exists.",'error')
        elif 'part_types.artikelnummer' in err_msg and (artikelnummer != original_db_an or (artikelnummer and not original_db_an)): flash(f"Error: Artikelnummer '{artikelnummer}' already exists.",'error')
        else: flash(f"DB integrity error: {e}",'error')
    except sqlite3.Error as e:
        if conn: conn.rollback(); flash(f"DB error: {e}",'error'); print(e,file=sys.stderr)
    except Exception as e:
        if conn: conn.rollback(); flash(f"Unexpected error: {e}","error"); print(e,file=sys.stderr)
    form_data_dict_err = {k:v for k,v in request.form.items()}; form_data_dict_err['id'] = part_type_id
    form_data_dict_err['part_type'] = part_type_category
    return render_template('edit_part_type.html',part_type=form_data_dict_err,part_types_categories=PART_TYPES_CATEGORIES),500

@app.route('/receive', methods=['GET'])
def receive_stock_form():
    part_types_list = []
    try:
        cursor = get_db().cursor()
        cursor.execute("SELECT id,part_name,part_number,artikelnummer,brand,model FROM part_types ORDER BY brand,model,part_name ASC")
        part_types_list = cursor.fetchall()
    except sqlite3.Error as e: flash(f"Error loading part types: {e}","error"); print(e,file=sys.stderr)
    except Exception as e: flash(f"Unexpected error: {e}","error"); print(e,file=sys.stderr)
    return render_template('receive_stock.html',part_types_list=part_types_list,submitted_order_number=request.form.get('order_number',''),submitted_order_date=request.form.get('order_date',''),submitted_notes=request.form.get('order_notes',''))

@app.route('/receive', methods=['POST'])
def receive_stock():
    conn = get_db(); cursor = conn.cursor(); items_created_count = 0; errors = []; lines_to_process = []
    order_number_ref = request.form.get('order_number','').strip() or None
    order_notes = request.form.get('order_notes','').strip() or None
    order_date_str = request.form.get('order_date','').strip(); order_date_to_insert = None
    if order_date_str:
        try: dt_obj = datetime.datetime.strptime(order_date_str,'%Y-%m-%d'); order_date_to_insert = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError: errors.append("Invalid Order Date format. Use YYYY-MM-DD.")
    for key,qty_received_str in request.form.items():
        if key.startswith('quantity_') and qty_received_str.strip():
            try:
                part_type_id = int(key.split('_')[1]); received_qty = int(qty_received_str.strip())
                if received_qty < 0: errors.append(f"Quantity for Part ID {part_type_id} cannot be negative.")
                elif received_qty > 0: lines_to_process.append({'part_type_id':part_type_id,'qty':received_qty})
            except ValueError: errors.append(f"Invalid quantity for '{key}'.")
            except IndexError: errors.append(f"Malformed key: '{key}'.")
    if errors:
        flash_errors(errors); part_types_list_form = []
        try:
            temp_cursor = get_db().cursor()
            temp_cursor.execute("SELECT id,part_name,part_number,artikelnummer,brand,model FROM part_types ORDER BY brand,model,part_name ASC")
            part_types_list_form = temp_cursor.fetchall()
        except sqlite3.Error as e_fetch: print(f"Error re-fetching part types: {e_fetch}",file=sys.stderr)
        return render_template('receive_stock.html',part_types_list=part_types_list_form,submitted_order_number=order_number_ref or '',submitted_order_date=order_date_str,submitted_notes=order_notes or ''),400
    if not lines_to_process: flash("No positive stock quantities entered.",'warning'); return redirect(url_for('receive_stock_form'))
    try:
        cursor.execute("BEGIN TRANSACTION")
        if order_date_to_insert: cursor.execute("INSERT INTO stock_orders (order_number,notes,order_date) VALUES (?,?,?)",(order_number_ref,order_notes,order_date_to_insert))
        else: cursor.execute("INSERT INTO stock_orders (order_number,notes,order_date) VALUES (?,?,CURRENT_TIMESTAMP)",(order_number_ref,order_notes))
        stock_order_id = cursor.lastrowid
        if not stock_order_id: raise sqlite3.Error("Failed to create stock order.")
        for line in lines_to_process:
            part_type_id,qty_received = line['part_type_id'],line['qty']
            cursor.execute("INSERT INTO stock_order_lines (stock_order_id,part_id,quantity_received) VALUES (?,?,?)",(stock_order_id,part_type_id,qty_received))
            line_id = cursor.lastrowid
            if not line_id: raise sqlite3.Error(f"Failed to create stock order line for part ID {part_type_id}.")
            for _ in range(qty_received):
                cursor.execute("INSERT INTO inventory_items (part_type_id,status,stock_order_line_id,date_received,last_updated) VALUES (?, 'Available', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",(part_type_id,line_id)); items_created_count +=1
        conn.commit()
        flash(f"Stock received for '{order_number_ref or '(No Ref)'}'. {items_created_count} item(s) added.",'success'); return redirect(url_for('orders_overview',search_term=order_number_ref or ''))
    except sqlite3.Error as e:
        if conn: conn.rollback(); print(f"DB Error: {e}",file=sys.stderr); flash(f"DB error: {e}",'error')
    except Exception as e:
        if conn: conn.rollback(); print(f"Error: {e}",file=sys.stderr); flash(f"Unexpected error: {e}",'error')
    part_types_list_err = []
    try:
        temp_cursor_err = get_db().cursor()
        temp_cursor_err.execute("SELECT id,part_name,part_number,artikelnummer,brand,model FROM part_types ORDER BY brand,model,part_name ASC")
        part_types_list_err = temp_cursor_err.fetchall()
    except sqlite3.Error as e_fetch_err: print(f"Error re-fetching part types: {e_fetch_err}",file=sys.stderr)
    return render_template('receive_stock.html',part_types_list=part_types_list_err,submitted_order_number=order_number_ref or '',submitted_order_date=order_date_str,submitted_notes=order_notes or ''),500

@app.route('/orders')
def orders_overview():
    order_lines = []; search_term = request.args.get('search_term','').strip()
    try:
        conn = get_db(); cursor = conn.cursor()
        sql = """SELECT so.id as order_id,so.order_number,so.order_date,so.notes AS order_notes,sol.id as line_id,sol.quantity_received,sol.cost_price_per_unit,
                   pt.id as part_type_id,pt.part_name,pt.part_number,pt.artikelnummer,pt.brand,pt.model
                   FROM stock_order_lines sol JOIN stock_orders so ON sol.stock_order_id=so.id JOIN part_types pt ON sol.part_id=pt.id WHERE 1=1 """
        params = []
        if search_term:
            search_like = f"%{search_term}%"; sql_addition = " AND (LOWER(IFNULL(so.order_number,'')) LIKE LOWER(?) OR LOWER(IFNULL(pt.artikelnummer,'')) LIKE LOWER(?) OR LOWER(IFNULL(pt.part_number,'')) LIKE LOWER(?) OR LOWER(pt.part_name) LIKE LOWER(?) OR LOWER(IFNULL(pt.brand,'')) LIKE LOWER(?) OR LOWER(IFNULL(pt.model,'')) LIKE LOWER(?) OR LOWER(IFNULL(so.notes,'')) LIKE LOWER(?))"
            sql += sql_addition; params.extend([search_like]*7)
        sql += " ORDER BY so.order_date DESC,so.id DESC,sol.id ASC"; cursor.execute(sql,params); order_lines = cursor.fetchall()
    except sqlite3.Error as e: print(f"DB Error: {e}",file=sys.stderr); flash(f"Error: {e}","error")
    except Exception as e: print(f"Error: {e}",file=sys.stderr); flash("Unexpected error.","error")
    return render_template('orders_overview.html',order_lines=order_lines,search_term=search_term)

@app.route('/bookings/add', methods=['GET'])
def add_booking_form():
    brands = []
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT brand FROM part_types WHERE brand IS NOT NULL AND brand != '' ORDER BY brand ASC")
        brands = [row['brand'] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        flash(f"Error loading brands: {e}", "error")
        print(f"Error loading brands for add_booking_form: {e}", file=sys.stderr)

    return render_template('add_booking.html',
                           available_items=[],
                           submitted_data=request.form if request.method == 'POST' else {},
                           brands_for_filter=brands)

@app.route('/api/parts_for_device', methods=['GET'])
def api_parts_for_device():
    model_query = request.args.get('model', '').strip()
    brand_query = request.args.get('brand', '').strip()

    # Als beide leeg zijn, retourneer een lege lijst (JS handelt dit af)
    if not model_query and not brand_query:
        return jsonify([])

    conn = get_db()
    if not conn:
        return jsonify({"error_message": "DB connection failed."}), 500

    parts_for_model = []
    try:
        cursor = conn.cursor()
        sql_query_base = """
            SELECT i.id, pt.part_name, pt.brand, pt.model, i.serial_number,
                   pt.part_number, pt.artikelnummer
            FROM inventory_items i
            JOIN part_types pt ON i.part_type_id = pt.id
            WHERE i.status = 'Available'
        """
        params = []
        conditions = []

        if brand_query:
            conditions.append("LOWER(pt.brand) = LOWER(?)")
            params.append(brand_query)

        if model_query:
            # Alleen filteren op model als er ook daadwerkelijk iets is ingevuld
            conditions.append("LOWER(pt.model) LIKE LOWER(?)")
            params.append(f"%{model_query}%")

        if conditions:
            sql_query_base += " AND " + " AND ".join(conditions)

        sql_query_base += " ORDER BY pt.brand, pt.model, pt.part_name, i.id;"

        cursor.execute(sql_query_base, tuple(params))
        rows = cursor.fetchall()
        for row in rows:
            parts_for_model.append(dict(row))

        return jsonify(parts_for_model)

    except sqlite3.Error as e:
        print(f"DB Error /api/parts_for_device: {e}", file=sys.stderr)
        return jsonify({"error_message": f"DB error: {e}"}), 500
    except Exception as e:
        print(f"Error /api/parts_for_device: {e}", file=sys.stderr)
        return jsonify({"error_message": f"Unexpected error: {e}"}), 500

@app.route('/bookings/add', methods=['POST'])
def add_booking():
    conn = get_db(); cursor = conn.cursor()
    customer_name = request.form.get('customer_name', '').strip()
    customer_phone = request.form.get('customer_phone', '').strip() or None
    device_model = request.form.get('device_model', '').strip()
    device_serial = request.form.get('device_serial', '').strip() or None
    gpc_number = request.form.get('gpc_number', '').strip() or None
    zir_reference = request.form.get('zir_reference', '').strip() or None
    reported_issue = request.form.get('reported_issue', '').strip()
    notes = request.form.get('notes', '').strip() or None
    booking_date_str = request.form.get('booking_date', '').strip()
    selected_item_id_str = request.form.get('inventory_item_id', '').strip()
    errors = []

    if not customer_name: errors.append("Customer Name required.")
    if not device_model: errors.append("Device Model required.") # Brand is voor filteren, model is essentieel voor de boeking zelf
    if not reported_issue: errors.append("Reported Issue required.")

    booking_date_to_insert = None
    if booking_date_str:
        try:
            dt_obj = datetime.datetime.strptime(booking_date_str, '%Y-%m-%d')
            booking_date_to_insert = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError: errors.append("Invalid Booking Date format. Please use YYYY-MM-DD.")

    selected_item_id = None
    if selected_item_id_str:
        try: selected_item_id = int(selected_item_id_str)
        except ValueError: errors.append("Invalid Inventory Item selected.")

    if errors:
        flash_errors(errors)
        # Re-fetch brands for form repopulation on error
        brands_for_form_repopulation = []
        try:
            conn_repop = get_db()
            cursor_repop = conn_repop.cursor()
            cursor_repop.execute("SELECT DISTINCT brand FROM part_types WHERE brand IS NOT NULL AND brand != '' ORDER BY brand ASC")
            brands_for_form_repopulation = [row['brand'] for row in cursor_repop.fetchall()]
        except sqlite3.Error as e_repop:
            print(f"Error re-fetching brands for form repopulation: {e_repop}", file=sys.stderr)
        return render_template('add_booking.html',
                               submitted_data=request.form,
                               available_items=[],
                               brands_for_filter=brands_for_form_repopulation), 400
    new_booking_id = None
    try:
        cursor.execute("BEGIN TRANSACTION")
        cols = ["customer_name", "customer_phone", "device_model", "device_serial",
                "gpc_number", "zir_reference", "reported_issue", "notes", "last_updated"]
        placeholders = ["?", "?", "?", "?", "?", "?", "?", "?", "CURRENT_TIMESTAMP"]
        vals = [customer_name, customer_phone, device_model, device_serial,
                gpc_number, zir_reference, reported_issue, notes]
        if booking_date_to_insert:
            cols.append("booking_date"); placeholders.append("?"); vals.append(booking_date_to_insert)
        else:
            cols.append("booking_date"); placeholders.append("CURRENT_TIMESTAMP")

        sql_booking = f"INSERT INTO bookings ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
        cursor.execute(sql_booking, tuple(vals))
        new_booking_id = cursor.lastrowid
        if not new_booking_id: raise sqlite3.Error("Failed to create booking record (no lastrowid).")

        if selected_item_id:
            cursor.execute("SELECT status FROM inventory_items WHERE id = ?", (selected_item_id,))
            item_row = cursor.fetchone()
            if not item_row: raise ValueError(f"Selected Inventory Item ID {selected_item_id} not found.")
            if item_row['status'] != 'Available':
                raise ValueError(f"Item (ID: {selected_item_id}) is not 'Available' (current status: {item_row['status']}). Cannot reserve.")
            cursor.execute("INSERT INTO booking_parts_used (booking_id, inventory_item_id) VALUES (?, ?)", (new_booking_id, selected_item_id))
            cursor.execute("UPDATE inventory_items SET status = 'Reserved', last_updated = CURRENT_TIMESTAMP WHERE id = ?", (selected_item_id,))
            if cursor.rowcount == 0: raise sqlite3.Error(f"Failed to update status to 'Reserved' for Item ID {selected_item_id}.")

        conn.commit()
        flash(f"Booking added (ID: {new_booking_id}). {'Item assigned and status set to Reserved.' if selected_item_id else 'No item assigned.'}", 'success')
        return redirect(url_for('bookings_overview'))
    except ValueError as e:
        if conn: conn.rollback()
        errors.append(str(e))
        flash_errors(errors)
    except sqlite3.Error as e:
        if conn: conn.rollback()
        print(f"DB Error adding booking: {e}", file=sys.stderr)
        flash(f"Database error adding booking: {e}", 'error')
    except Exception as e:
        if conn: conn.rollback()
        print(f"Unexpected error adding booking: {e}", file=sys.stderr)
        flash(f"Unexpected error adding booking: {e}", 'error')

    status_code = 400 if errors else 500
    # Re-fetch brands for form repopulation on exception
    brands_for_form_repopulation_exc = []
    try:
        conn_repop_exc = get_db()
        cursor_repop_exc = conn_repop_exc.cursor()
        cursor_repop_exc.execute("SELECT DISTINCT brand FROM part_types WHERE brand IS NOT NULL AND brand != '' ORDER BY brand ASC")
        brands_for_form_repopulation_exc = [row['brand'] for row in cursor_repop_exc.fetchall()]
    except sqlite3.Error as e_repop_exc:
        print(f"Error re-fetching brands for form repopulation after exception: {e_repop_exc}", file=sys.stderr)

    return render_template('add_booking.html',
                           submitted_data=request.form,
                           available_items=[],
                           brands_for_filter=brands_for_form_repopulation_exc), status_code

@app.route('/bookings')
def bookings_overview():
    bookings_processed = []; search_term = request.args.get('search_booking', '').strip()
    try:
        conn = get_db(); cursor = conn.cursor()
        sql = "SELECT id, booking_date, customer_name, device_model, device_serial, status, notes, gpc_number, zir_reference, last_updated FROM bookings WHERE 1=1 "
        params = []
        if search_term:
            search_pattern = f"%{search_term}%"; sql += "AND (LOWER(customer_name) LIKE LOWER(?) OR LOWER(device_model) LIKE LOWER(?) OR CAST(id AS TEXT) LIKE ? OR LOWER(IFNULL(gpc_number,'')) LIKE LOWER(?) OR LOWER(IFNULL(zir_reference,'')) LIKE LOWER(?) OR LOWER(IFNULL(device_serial,'')) LIKE LOWER(?) OR LOWER(IFNULL(notes,'')) LIKE LOWER(?))"
            params.extend([search_pattern] * 7)
        sql += " ORDER BY booking_date DESC, id DESC"; cursor.execute(sql, params); bookings_raw = cursor.fetchall()
        now_naive = datetime.datetime.now()
        for booking_row in bookings_raw:
            booking_dict = dict(booking_row); months_in_system = 0; booking_date_str = booking_dict.get('booking_date')
            if booking_date_str:
                try:
                    booking_date_dt_naive = datetime.datetime.strptime(booking_date_str.split('.')[0], '%Y-%m-%d %H:%M:%S'); delta = relativedelta(now_naive, booking_date_dt_naive)
                    months_in_system = delta.years * 12 + delta.months
                except (ValueError, TypeError) as e_date_parse: print(f"Warning: Could not parse booking_date '{booking_date_str}' for ID {booking_dict.get('id')}: {e_date_parse}", file=sys.stderr)
            booking_dict['months_in_system'] = months_in_system; bookings_processed.append(booking_dict)
    except sqlite3.Error as e: print(f"DB Error: {e}", file=sys.stderr); flash(f"Error: {e}", "error")
    except Exception as e: print(f"Error: {e}", file=sys.stderr); flash("Unexpected error.", "error")
    return render_template('bookings_overview.html', bookings=bookings_processed, search_term=search_term, allowed_booking_statuses=ALLOWED_BOOKING_STATUSES)

@app.route('/booking/<int:booking_id>/edit', methods=['GET'])
def edit_booking_form(booking_id):
    booking_data = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id, booking_date, customer_name, customer_phone, device_model, device_serial, gpc_number, zir_reference, reported_issue, status, notes FROM bookings WHERE id = ?", (booking_id,))
        booking_data = cursor.fetchone()
        if not booking_data: flash(f"Booking ID {booking_id} not found.", "error"); return redirect(url_for('bookings_overview'))
    except sqlite3.Error as e: flash(f"Error loading details: {e}", "error"); return redirect(url_for('bookings_overview'))
    except Exception as e: flash(f"Unexpected error: {e}", "error"); return redirect(url_for('bookings_overview') )
    return render_template('edit_booking.html', booking=booking_data, allowed_booking_statuses=ALLOWED_BOOKING_STATUSES)

@app.route('/receive_fast', methods=['GET'])
def receive_stock_fast_form():
    return render_template('receive_stock_fast.html', submitted_data=request.form if request.method == 'POST' else {})

@app.route('/receive_fast', methods=['POST'])
def receive_stock_fast():
    conn = get_db(); cursor = conn.cursor(); items_created_count = 0; errors = []; line_item_errors = []
    order_number_ref = request.form.get('order_number', '').strip() or None; order_notes = request.form.get('order_notes', '').strip() or None
    order_date_str = request.form.get('order_date', '').strip(); part_identifiers = request.form.getlist('part_identifier[]'); quantities_str = request.form.getlist('quantity[]')
    order_date_to_insert = None
    if order_date_str:
        try: dt_obj = datetime.datetime.strptime(order_date_str, '%Y-%m-%d'); order_date_to_insert = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError: errors.append("Invalid Order Date format.")
    if not part_identifiers or not quantities_str or len(part_identifiers) != len(quantities_str): errors.append("Part identifier/quantity mismatch.")
    if not any(pi.strip() for pi in part_identifiers) and not errors: errors.append("At least one part identifier required.")
    lines_to_process = []; submitted_items_for_repopulation = []
    for i, identifier in enumerate(part_identifiers):
        identifier = identifier.strip(); quantity_str = quantities_str[i].strip() if i < len(quantities_str) else ""
        submitted_items_for_repopulation.append({'part_identifier': identifier, 'quantity': quantity_str})
        if not identifier and not quantity_str: continue
        if not identifier and quantity_str: line_item_errors.append(f"Row {i+1}: Identifier missing, quantity '{quantity_str}'."); continue
        if identifier and not quantity_str: line_item_errors.append(f"Row {i+1}: Quantity missing for '{identifier}'."); continue
        try:
            quantity = int(quantity_str)
            if quantity <= 0: line_item_errors.append(f"Row {i+1}: Quantity for '{identifier}' must be positive."); continue
        except ValueError: line_item_errors.append(f"Row {i+1}: Invalid quantity '{quantity_str}' for '{identifier}'."); continue
        cursor.execute("SELECT id, part_name FROM part_types WHERE part_number = ? OR artikelnummer = ?", (identifier, identifier))
        part_type_row = cursor.fetchone()
        if not part_type_row: line_item_errors.append(f"Row {i+1}: Part '{identifier}' not found."); continue
        lines_to_process.append({'part_type_id': part_type_row['id'], 'part_name': part_type_row['part_name'], 'identifier_used': identifier, 'qty': quantity})
    if errors or line_item_errors:
        for err in errors: flash(err, 'error');
        for err in line_item_errors: flash(err, 'error')
        submitted_data_repop = {'order_number':order_number_ref,'order_date':order_date_str,'order_notes':order_notes,'items':submitted_items_for_repopulation}
        return render_template('receive_stock_fast.html', submitted_data=submitted_data_repop), 400
    if not lines_to_process:
        flash("No valid items to process.", 'warning')
        submitted_data_repop_novalid = {'order_number':order_number_ref,'order_date':order_date_str,'order_notes':order_notes,'items':submitted_items_for_repopulation}
        return render_template('receive_stock_fast.html', submitted_data=submitted_data_repop_novalid), 400
    try:
        cursor.execute("BEGIN TRANSACTION")
        if order_date_to_insert: cursor.execute("INSERT INTO stock_orders (order_number, notes, order_date) VALUES (?, ?, ?)", (order_number_ref, order_notes, order_date_to_insert))
        else: cursor.execute("INSERT INTO stock_orders (order_number, notes, order_date) VALUES (?, ?, CURRENT_TIMESTAMP)", (order_number_ref, order_notes))
        stock_order_id = cursor.lastrowid
        if not stock_order_id: raise sqlite3.Error("Failed to create stock order.")
        for line in lines_to_process:
            part_type_id, qty_received = line['part_type_id'], line['qty']
            cursor.execute("INSERT INTO stock_order_lines (stock_order_id, part_id, quantity_received) VALUES (?, ?, ?)", (stock_order_id, part_type_id, qty_received))
            line_id = cursor.lastrowid
            if not line_id: raise sqlite3.Error(f"Failed to create order line for part ID {part_type_id}.")
            for _ in range(qty_received):
                cursor.execute("INSERT INTO inventory_items (part_type_id, status, stock_order_line_id, date_received, last_updated) VALUES (?, 'Available', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",(part_type_id, line_id)); items_created_count += 1
        conn.commit(); flash(f"Stock received for '{order_number_ref or '(No Ref)'}'. {items_created_count} item(s) added.", 'success'); return redirect(url_for('orders_overview', search_term=order_number_ref or ''))
    except sqlite3.Error as e:
        if conn: conn.rollback(); print(f"DB Error: {e}", file=sys.stderr); flash(f"DB error: {e}", 'error')
    except Exception as e:
        if conn: conn.rollback(); print(f"Error: {e}", file=sys.stderr); flash(f"Unexpected error: {e}", 'error')
    submitted_data_repop_error = {'order_number':order_number_ref,'order_date':order_date_str,'order_notes':order_notes,'items':submitted_items_for_repopulation}
    return render_template('receive_stock_fast.html', submitted_data=submitted_data_repop_error), 500

@app.route('/booking/<int:booking_id>/edit', methods=['POST'])
def update_booking(booking_id):
    new_status = request.form.get('status', '').strip()
    conn = get_db(); cursor = conn.cursor(); error_page_redirect_func = None
    is_from_full_edit_page = 'submit_edit_booking_details' in request.form
    try:
        if is_from_full_edit_page:
            new_notes = request.form.get('notes', '').strip() or None; new_gpc_number = request.form.get('gpc_number', '').strip() or None
            new_zir_reference = request.form.get('zir_reference', '').strip() or None
            if not new_status or new_status not in ALLOWED_BOOKING_STATUSES: flash("Invalid status.", "error"); return redirect(url_for('edit_booking_form', booking_id=booking_id))
            sql = "UPDATE bookings SET status = ?, notes = ?, gpc_number = ?, zir_reference = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?"
            params = (new_status, new_notes, new_gpc_number, new_zir_reference, booking_id)
            success_redirect_url = url_for('bookings_overview'); error_page_redirect_func = lambda: redirect(url_for('edit_booking_form', booking_id=booking_id))
        else:
            flash("Invalid form submission for booking update. Please use the edit page.", "error")
            return redirect(url_for('bookings_overview'))

        cursor.execute("BEGIN TRANSACTION"); cursor.execute(sql, params)
        booking_updated = cursor.rowcount > 0
        if not booking_updated: flash(f"Booking ID {booking_id} not found or no changes made.", "warning")
        else: flash(f"Booking ID {booking_id} updated. Status: '{new_status}'.", "success")
        if booking_updated and new_status == 'Completed':
            cursor.execute("SELECT inventory_item_id FROM booking_parts_used WHERE booking_id = ?", (booking_id,)); items_to_update = cursor.fetchall(); updated_item_count = 0
            for item_row in items_to_update:
                item_id_to_update = item_row['inventory_item_id']
                cursor.execute("UPDATE inventory_items SET status = 'Installed', last_updated = CURRENT_TIMESTAMP WHERE id = ? AND status != 'Installed'", (item_id_to_update,))
                if cursor.rowcount > 0: updated_item_count += 1
            if updated_item_count > 0: flash(f"{updated_item_count} part(s) for Booking ID {booking_id} set to 'Installed'.", "info")
        conn.commit(); return redirect(success_redirect_url)
    except sqlite3.Error as e:
        if conn: conn.rollback(); flash(f"DB error: {e}", "error"); print(f"SQLite Error booking update (ID: {booking_id}): {e}", file=sys.stderr)
        return error_page_redirect_func() if error_page_redirect_func else redirect(url_for('bookings_overview'))
    except Exception as e:
        if conn: conn.rollback(); flash(f"Unexpected error: {e}", "error"); print(f"Unexpected Error booking update (ID: {booking_id}): {e}", file=sys.stderr)
        return error_page_redirect_func() if error_page_redirect_func else redirect(url_for('bookings_overview'))

def get_schema_version(conn_to_check):
    cursor = conn_to_check.cursor()
    try:
        cursor.execute("SELECT version FROM schema_version LIMIT 1"); result = cursor.fetchone(); return result['version'] if result else 0
    except sqlite3.Error:
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
            if not cursor.fetchone(): return 0
            else: print("Warning: schema_version table exists but 'SELECT version' failed. Assuming 0.", file=sys.stderr); return 0
        except sqlite3.Error as e_check: print(f"Error checking schema_version table: {e_check}. Assuming 0.", file=sys.stderr); return 0

if __name__ == '__main__':
    try: from dateutil.relativedelta import relativedelta
    except ImportError: print("ERROR: 'python-dateutil' not found. pip install python-dateutil"); sys.exit(1)
    if not os.path.exists(DATABASE): print(f"CRITICAL: DB '{DATABASE}' not found. Run database_setup.py.", file=sys.stderr); sys.exit(1)
    temp_conn_for_check = None
    try:
        temp_conn_for_check = sqlite3.connect(DATABASE); temp_conn_for_check.row_factory = sqlite3.Row; current_ver = get_schema_version(temp_conn_for_check)
        if current_ver < DB_SCHEMA_REQ: print(f"WARNING: DB schema ({current_ver}) < required ({DB_SCHEMA_REQ}). Run database_setup.py.", file=sys.stderr)
        elif current_ver > DB_SCHEMA_REQ: print(f"WARNING: DB schema ({current_ver}) > expected ({DB_SCHEMA_REQ}). App might malfunction.", file=sys.stderr)
        else: print(f"DB schema version ({current_ver}) is compatible.")
    except Exception as e: print(f"Error checking DB version: {e}", file=sys.stderr); sys.exit(1) # Exit on DB check error
    finally:
        if temp_conn_for_check: temp_conn_for_check.close()
    host = os.environ.get('FLASK_RUN_HOST', os.environ.get('IP', '0.0.0.0'))
    port = int(os.environ.get('FLASK_RUN_PORT', os.environ.get('PORT', 5000)))
    debug_mode_str = os.environ.get('FLASK_DEBUG', '1'); debug_mode = debug_mode_str.lower() not in ['0', 'false', 'no']
    print(f"Starting Flask server on http://{host}:{port}/ (or http://127.0.0.1:{port}/)")
    print(f"Flask Debug Mode: {'On' if debug_mode else 'Off'}")
    app.run(debug=debug_mode, host=host, port=port)