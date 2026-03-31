from flask import Flask, request, jsonify, Response
from sqlalchemy import create_engine, MetaData, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DatabaseError
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity, get_jwt
from datetime import timedelta, datetime, timezone
from functools import wraps
from werkzeug.exceptions import HTTPException
import logging
import logging.handlers
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import csv, io
import time
import tempfile

# Configure logging to write to syslog
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Changed to INFO to see progress logs
syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
formatter = logging.Formatter('%(name)s: %(levelname)s %(message)s')
syslog_handler.setFormatter(formatter)
logger.addHandler(syslog_handler)

# Configure mysql database URL for application
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'supersecretapikeychangeme')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)

APP_VERSION = "0.1.0"

jwt = JWTManager(app)

# GALERA-OPTIMIZED ENGINE CONFIGURATION
engine = create_engine(
    'mysql://md:bibleblack@b2b-aaa/radius',
    pool_size=15,                    # Larger pool for high read traffic
    pool_recycle=300,                # Recycle connections every 5 minutes
    pool_pre_ping=True,              # CRITICAL: Test connections before use
    max_overflow=25,                 # Allow burst traffic
    pool_timeout=30,
    isolation_level='READ COMMITTED', # Better for Galera than default
    echo=False,  # Set to True for debugging SQL
    connect_args={
        'local_infile': True          # Enable LOAD DATA LOCAL INFILE
    }
)

# Use scoped session for thread safety
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)

Base = declarative_base()

ALLOWED_TABLES = {'radcheck','radreply','radusergroup','radgroupcheck','radgroupreply','prohibited','whiteboned'}

# SQLite connection for user credentials
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_DB_PATH = os.path.join(BASE_DIR, 'user_credentials.db')

def get_sqlite_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite_db():
    conn = get_sqlite_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS users
                    (username TEXT PRIMARY KEY, password TEXT)''')
    conn.close()


class ApiError(HTTPException):
    def __init__(self, message, status_code=400, error_type="API Error", details=None):
        super().__init__(description=message)
        self.message = message
        self.status_code = status_code
        self.code = status_code
        self.error_type = error_type
        self.details = details


def success_response(payload=None, status_code=200):
    body = {"success": True}
    if payload:
        body.update(payload)
    return jsonify(body), status_code


def validate_table_name(table_name):
    if table_name not in ALLOWED_TABLES:
        raise ApiError(
            f"Table {table_name} is not allowed",
            status_code=400,
            error_type="Validation Error",
        )


def get_json_body(required=False):
    data = request.get_json(silent=True)
    if required and not data:
        raise ApiError("No JSON body provided", status_code=400, error_type="Validation Error")
    return data


def get_uploaded_csv():
    if 'file' not in request.files:
        raise ApiError("No file part", status_code=400, error_type="Validation Error")

    file = request.files['file']
    if file.filename == '':
        raise ApiError("No selected file", status_code=400, error_type="Validation Error")
    if not file.filename.endswith('.csv'):
        raise ApiError("File must be a CSV", status_code=400, error_type="Validation Error")

    return file


@app.errorhandler(ApiError)
def handle_api_error(error):
    payload = {
        "success": False,
        "error": error.message,
        "error_type": error.error_type,
    }
    if error.details is not None:
        payload["details"] = error.details
    logger.warning(f"{error.error_type}: {error.message}")
    return Response(
        json.dumps(payload, default=str),
        status=error.status_code,
        mimetype='application/json',
    )


@app.errorhandler(SQLAlchemyError)
def handle_sqlalchemy_error(error):
    try:
        Session.rollback()
    except Exception:
        pass
    logger.exception("Database error")
    return jsonify({
        "success": False,
        "error": "Database error occurred",
        "error_type": "Database Error",
    }), 500


@app.errorhandler(HTTPException)
def handle_http_error(error):
    return jsonify({
        "success": False,
        "error": error.description,
        "error_type": "HTTP Error",
    }), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    logger.exception("Unexpected error")
    return jsonify({
        "success": False,
        "error": "An unexpected error occurred",
        "error_type": "Unexpected Error",
    }), 500

# GALERA RETRY DECORATOR
def galera_retry(max_retries=5):
    """Retry decorator for handling Galera conflicts and deadlocks"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DatabaseError) as e:
                    # Extract error code
                    error_code = None
                    if hasattr(e, 'orig') and hasattr(e.orig, 'args') and e.orig.args:
                        error_code = str(e.orig.args[0])
                    
                    error_str = str(e)
                    
                    # Check for Galera-specific errors
                    # 1213: Deadlock
                    # 2013: Lost connection during query
                    # 1205: Lock wait timeout
                    is_galera_error = (
                        error_code in ['1213', '2013', '1205'] or
                        'wsrep' in error_str.lower() or
                        'deadlock' in error_str.lower()
                    )
                    
                    if is_galera_error and attempt < max_retries - 1:
                        # Rollback the failed transaction
                        try:
                            Session.rollback()
                        except:
                            pass
                        
                        # Exponential backoff with jitter
                        sleep_time = (0.1 * (2 ** attempt)) + (time.time() % 0.05)
                        logger.warning(
                            f"Galera conflict in {func.__name__}, "
                            f"retry {attempt+1}/{max_retries} after {sleep_time:.2f}s - Error: {error_code}"
                        )
                        time.sleep(sleep_time)
                        continue
                    
                    # Not a Galera error or out of retries
                    try:
                        Session.rollback()
                    except:
                        pass
                    raise
                    
            return None
        return wrapper
    return decorator

# TEARDOWN - Clean up sessions after each request
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Remove database sessions at the end of the request"""
    Session.remove()

@app.route('/register', methods=['POST'])
def register():
    logger.info(f"We expect credentials database at: {SQLITE_DB_PATH}")
    data = get_json_body(required=True)
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        raise ApiError("Missing username, password", status_code=400, error_type="Validation Error")

    conn = get_sqlite_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        raise ApiError("Username already exists", status_code=400, error_type="Validation Error")

    hashed_password = generate_password_hash(password)
    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
    conn.commit()
    conn.close()
    return success_response({"message": "User created successfully"}, 201)

@app.route('/login', methods=['POST'])
def login():
    data = get_json_body(required=True)
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        raise ApiError("Missing username, password", status_code=400, error_type="Validation Error")

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user['password'], password):
        access_token = create_access_token(identity=username)
        return success_response({"access_token": access_token})

    raise ApiError("Bad username or password", status_code=401, error_type="Authentication Error")

@app.route('/token/check', methods=['GET'])
#@jwt_required()
def check_token():
    jwt_payload = get_jwt()
    exp_timestamp = jwt_payload['exp']
    exp_datetime = datetime.fromtimestamp(exp_timestamp, timezone.utc)
    current_time = datetime.now(timezone.utc)
    time_remaining = exp_datetime - current_time

    response = {
        "expires_at": exp_datetime.isoformat(),
        "seconds_remaining": time_remaining.total_seconds(),
        "is_expired": time_remaining.total_seconds() <= 0
    }

    return success_response(response)

@app.route('/')
#@jwt_required()
def index():
    current_user = get_jwt_identity()
    return success_response({
        "logged_in_as": current_user,
        "version": APP_VERSION,
    })

@app.route('/hello')
def hello():
    return success_response({
        "message": "Hello,Dude!",
        "version": APP_VERSION,
    })

@app.route('/version', methods=['GET'])
def version():
    return success_response({"version": APP_VERSION})

@app.route('/select/<table_name>', methods=['GET'])
#@jwt_required()
@galera_retry(max_retries=3)
def select_from_table(table_name):
    validate_table_name(table_name)

    columns = request.args.get('columns', '*')
    where_clause = request.args.get('where', '')
    order_by = request.args.get('order_by', '')
    limit = request.args.get('limit', '')
    
    query = f"SELECT {columns} FROM {table_name}"
    if where_clause:
        query += f" WHERE {where_clause}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit:
        query += f" LIMIT {limit}"
    
    session = Session()
    result = session.execute(text(query))
    column_names = result.keys()
    data = [dict(zip(column_names, row)) for row in result.fetchall()]
    
    return Response(json.dumps({"success": True, "data": data}, default=str), mimetype='application/json'), 200

def create_table_class(table_name):
    metadata = MetaData()
    inspector = inspect(engine)

    if not inspector.has_table(table_name):
        raise ApiError(
            f"Table {table_name} does not exist in the database",
            status_code=404,
            error_type="Validation Error",
        )

    try:
        metadata.reflect(engine, only=[table_name])
        table = metadata.tables[table_name]

        class DynamicTable(Base):
            __table__ = table

        return DynamicTable
    except Exception as e:
        raise ApiError(
            f"Error reflecting table {table_name}: {str(e)}",
            status_code=400,
            error_type="Validation Error",
        )

@galera_retry(max_retries=5)
def execute_delete_query(table_name, params):
    session = Session()

    try:
        DynamicTable = create_table_class(table_name)
        logger.debug(f"Table structure for {table_name}: {DynamicTable.__table__.columns}")

        query = session.query(DynamicTable)
        for key, value in params.items():
            if hasattr(DynamicTable, key):
                query = query.filter(getattr(DynamicTable, key) == value)
            else:
                raise ApiError(
                    f"Column {key} does not exist in table {table_name}",
                    status_code=400,
                    error_type="Validation Error",
                )

        logger.debug(f"Generated SQL: {query}")
        affected_rows = query.delete(synchronize_session=False)
        session.commit()

        logger.info(f"Delete operation successful. Affected rows: {affected_rows}")
        return {"message": f"Delete operation successful. Affected rows: {affected_rows}"}

    except SQLAlchemyError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

@app.route('/delete/<table_name>', methods=['POST'])
#@jwt_required()
def delete_route(table_name):
    validate_table_name(table_name)
    json_data = get_json_body(required=True)
    result = execute_delete_query(table_name, json_data)
    return success_response(result)

@app.route('/insert/<table_name>', methods=['POST'])
#@jwt_required()
@galera_retry(max_retries=5)
def insert_into_table(table_name):
    validate_table_name(table_name)
    data = get_json_body(required=True)

    columns = ', '.join(data.keys())
    values = ', '.join([':' + key for key in data.keys()])
    query = f"INSERT INTO {table_name} ({columns}) VALUES ({values})"

    with Session() as session:
        result = session.execute(text(query), data)
        session.commit()

    return success_response({"message": "Insert successful", "rows_affected": result.rowcount}, 201)

@app.route('/populate_old/<table_name>', methods=['POST'])
#@jwt_required()
@galera_retry(max_retries=5)
def populate_table_from_csv(table_name):
    """
    High-performance bulk population from CSV.
    Optimized for loading hundreds/thousands of records with minimal Galera conflicts.
    
    Uses multi-row INSERT statements for maximum efficiency.
    Query parameters:
    - batch_size: Number of rows per INSERT (default: 500, range: 50-1000)
    """
    validate_table_name(table_name)
    file = get_uploaded_csv()

    has_headers = request.form.get('has_headers', 'true').lower() == 'true'
    
    # Allow custom batch size via query parameter
    try:
        batch_size = int(request.args.get('batch_size', '500'))
        batch_size = min(max(batch_size, 50), 1000)  # Clamp between 50-1000
    except ValueError:
        batch_size = 500

    try:
        # Read CSV file
        csv_content = file.read().decode('utf-8')
        csv_file = io.StringIO(csv_content)

        if has_headers:
            reader = csv.DictReader(csv_file)
            columns = list(reader.fieldnames)
        else:
            reader = csv.reader(csv_file)
            with engine.connect() as connection:
                result = connection.execute(text(f"SHOW COLUMNS FROM {table_name}"))
                columns = [row[0] for row in result]

        logger.info(f"Bulk load to {table_name}, columns: {columns}")

        # Collect all data
        all_values = []
        for row in reader:
            if has_headers:
                row_data = [row.get(column, '') for column in columns]
            else:
                row_data = list(row)
            if any(row_data):  # Skip empty rows
                all_values.append(row_data)

        if not all_values:
            raise ApiError("No valid data found in the CSV file", status_code=400, error_type="Validation Error")

        total_rows = len(all_values)
        logger.info(f"Starting bulk insert: {total_rows} rows in batches of {batch_size}")

        # Prepare base query components
        columns_escaped = ', '.join(f'`{col}`' for col in columns)
        
        total_inserted = 0
        batches_completed = 0
        start_time = time.time()

        # Process in batches using multi-row INSERT
        for batch_start in range(0, total_rows, batch_size):
            batch_end = min(batch_start + batch_size, total_rows)
            batch = all_values[batch_start:batch_end]
            batch_len = len(batch)

            # Build multi-row INSERT VALUES clause
            # Much faster than individual INSERTs
            values_clauses = []
            flat_params = {}
            param_idx = 0
            
            for row in batch:
                row_placeholders = ', '.join([f':p{param_idx + i}' for i in range(len(columns))])
                values_clauses.append(f'({row_placeholders})')
                
                # Flatten row data into params dict
                for i, value in enumerate(row):
                    flat_params[f'p{param_idx + i}'] = value
                param_idx += len(columns)
            
            values_string = ', '.join(values_clauses)
            query = f"INSERT INTO `{table_name}` ({columns_escaped}) VALUES {values_string}"

            # Execute batch insert
            with engine.connect() as connection:
                trans = connection.begin()
                try:
                    connection.execute(text(query), flat_params)
                    trans.commit()
                    
                    total_inserted += batch_len
                    batches_completed += 1
                    
                    # Progress logging
                    if batches_completed % 10 == 0 or batch_end >= total_rows:
                        elapsed = time.time() - start_time
                        rate = total_inserted / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"Progress: {total_inserted}/{total_rows} rows "
                            f"({batches_completed} batches, {rate:.0f} rows/sec)"
                        )
                    
                    # Brief pause between batches to allow reads through
                    # Skip on last batch
                    if batch_end < total_rows:
                        time.sleep(0.005)  # 5ms pause
                        
                except Exception as e:
                    trans.rollback()
                    logger.error(f"Batch {batches_completed + 1} failed: {str(e)}")
                    raise

        elapsed_time = time.time() - start_time
        rows_per_sec = total_inserted / elapsed_time if elapsed_time > 0 else 0

        logger.info(
            f"Bulk load completed: {total_inserted} rows in {batches_completed} batches, "
            f"{elapsed_time:.2f}s ({rows_per_sec:.0f} rows/sec)"
        )
        
        return success_response({
            "message": "Data imported successfully",
            "rows_inserted": total_inserted,
            "batches": batches_completed,
            "elapsed_seconds": round(elapsed_time, 2),
            "rows_per_second": round(rows_per_sec, 0)
        }, 201)

    except csv.Error as e:
        raise ApiError(f"CSV parsing error: {str(e)}", status_code=400, error_type="Validation Error")

@app.route('/populate/<table_name>', methods=['POST'])
#@jwt_required()
@galera_retry(max_retries=5)
def populate_table_fast(table_name):
    """
    Ultra-fast bulk population using LOAD DATA LOCAL INFILE.
    Best for very large datasets (10,000+ rows).
    Automatically strips carriage returns (CR) from data.
    
    Note: Requires LOAD DATA LOCAL enabled in MariaDB and client.
    """
    validate_table_name(table_name)
    file = get_uploaded_csv()

    has_headers = request.form.get('has_headers', 'true').lower() == 'true'

    try:
        # Read file and normalize line endings
        csv_content = file.read().decode('utf-8')
        # Replace CRLF with LF, then CR with LF
        csv_content = csv_content.replace('\r\n', '\n').replace('\r', '')
        
        # Save normalized content to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as temp_file:
            temp_file.write(csv_content)
            temp_path = temp_file.name

        logger.info(f"Saved normalized CSV to temp file: {temp_path}")

        # Read the CSV headers to know which columns we're loading
        if has_headers:
            with open(temp_path, 'r') as f:
                csv_reader = csv.reader(f)
                csv_columns = next(csv_reader)  # Get column names from CSV
        else:
            # If no headers, get all columns from table
            with engine.connect() as connection:
                result = connection.execute(text(f"SHOW COLUMNS FROM `{table_name}`"))
                csv_columns = [row[0] for row in result]
        
        columns_escaped = ', '.join(f'`{col}`' for col in csv_columns)
        
        logger.info(f"Loading columns: {csv_columns}")

        start_time = time.time()

        # Use LOAD DATA INFILE for maximum performance
        with engine.connect() as connection:
            trans = connection.begin()
            try:
                # Build LOAD DATA query
                # Note: We already normalized line endings to \n in the temp file
                query = f"""
                    LOAD DATA LOCAL INFILE '{temp_path}'
                    INTO TABLE `{table_name}`
                    FIELDS TERMINATED BY ','
                    ENCLOSED BY '"'
                    LINES TERMINATED BY '\\n'
                    {'IGNORE 1 LINES' if has_headers else ''}
                    ({columns_escaped})
                """
                
                connection.execute(text(query))
                
                # Get row count
                result = connection.execute(text(f"SELECT ROW_COUNT()"))
                rows_inserted = result.scalar()
                
                trans.commit()
                
                elapsed_time = time.time() - start_time
                rows_per_sec = rows_inserted / elapsed_time if elapsed_time > 0 else 0

                logger.info(
                    f"LOAD DATA completed: {rows_inserted} rows in {elapsed_time:.2f}s "
                    f"({rows_per_sec:.0f} rows/sec)"
                )

                # Clean up temp file
                os.unlink(temp_path)

                return success_response({
                    "message": "Data imported successfully using LOAD DATA",
                    "rows_inserted": rows_inserted,
                    "elapsed_seconds": round(elapsed_time, 2),
                    "rows_per_second": round(rows_per_sec, 0)
                }, 201)

            except Exception as e:
                trans.rollback()
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

    except SQLAlchemyError as e:
        if '2068' in str(e) or 'LOAD DATA LOCAL INFILE file request rejected' in str(e):
            raise ApiError(
                "LOAD DATA LOCAL INFILE is disabled on the server or client",
                status_code=500,
                error_type="Database Error",
                details={"suggestion": "Use /populate_old endpoint instead"},
            )
        raise

@app.route('/truncate/<table_name>', methods=['POST'])
#@jwt_required()
@galera_retry(max_retries=5)
def truncate_table(table_name):
    """
    Truncate (delete all records from) a table and reset auto-increment counter.
    TRUNCATE automatically resets auto-increment and is faster than DELETE.
    WARNING: This operation cannot be undone!
    """
    validate_table_name(table_name)
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        raise ApiError(f"Table {table_name} does not exist", status_code=404, error_type="Validation Error")

    with engine.connect() as connection:
        connection.execute(text(f"TRUNCATE TABLE `{table_name}`"))
        connection.commit()

    logger.info(f"Truncate operation successful on table {table_name}")
    return success_response({"message": f"Table {table_name} truncated successfully"})

@app.route('/bulk_delete/<table_name>', methods=['POST'])
#@jwt_required()
@galera_retry(max_retries=5)
def bulk_delete_from_csv(table_name):
    """
    Delete multiple records from a table based on data from a CSV file.
    The CSV should contain columns that match the table's columns.
    Records matching the CSV data will be deleted in batches.
    """
    validate_table_name(table_name)
    file = get_uploaded_csv()

    has_headers = request.form.get('has_headers', 'true').lower() == 'true'

    try:
        inspector = inspect(engine)
        if not inspector.has_table(table_name):
            raise ApiError(f"Table {table_name} does not exist", status_code=404, error_type="Validation Error")

        csv_content = file.read().decode('utf-8')
        csv_file = io.StringIO(csv_content)

        if has_headers:
            reader = csv.DictReader(csv_file)
            columns = reader.fieldnames
        else:
            reader = csv.reader(csv_file)
            with engine.connect() as connection:
                result = connection.execute(text(f"SHOW COLUMNS FROM {table_name}"))
                columns = [row[0] for row in result]

        logger.info(f"Columns for bulk delete: {columns}")

        delete_criteria = []
        for row in reader:
            if has_headers:
                row_dict = {col: row.get(col, '') for col in columns if row.get(col, '')}
            else:
                row_dict = {columns[i]: val for i, val in enumerate(row) if i < len(columns) and val}

            if row_dict:
                delete_criteria.append(row_dict)

        if not delete_criteria:
            raise ApiError("No valid data found in the CSV file", status_code=400, error_type="Validation Error")

        logger.info(f"Number of records to delete: {len(delete_criteria)}")

        # Process deletes in batches
        BATCH_SIZE = 50
        total_deleted = 0
        batches_completed = 0

        DynamicTable = create_table_class(table_name)

        for batch_start in range(0, len(delete_criteria), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(delete_criteria))
            batch = delete_criteria[batch_start:batch_end]

            session = Session()
            try:
                for criteria in batch:
                    query = session.query(DynamicTable)

                    for key, value in criteria.items():
                        if hasattr(DynamicTable, key):
                            query = query.filter(getattr(DynamicTable, key) == value)

                    affected_rows = query.delete(synchronize_session=False)
                    total_deleted += affected_rows

                session.commit()
                batches_completed += 1
                
                logger.info(
                    f"Delete batch {batches_completed} completed: "
                    f"{total_deleted} total rows deleted"
                )

                # Brief pause between batches
                if batch_end < len(delete_criteria):
                    time.sleep(0.01)

            except Exception as e:
                session.rollback()
                raise
            finally:
                session.close()

        logger.info(f"Bulk delete completed: {total_deleted} rows in {batches_completed} batches")

        return success_response({
            "message": "Bulk delete operation successful",
            "rows_deleted": total_deleted,
            "records_processed": len(delete_criteria),
            "batches": batches_completed
        })

    except csv.Error as e:
        raise ApiError(f"CSV parsing error: {str(e)}", status_code=400, error_type="Validation Error")

@app.route('/export/<table_name>', methods=['GET'])
#@jwt_required()
@galera_retry(max_retries=3)
def export_table_to_csv(table_name):
    """
    Export table data to CSV file with LF-only line endings.
    Manually constructs CSV to guarantee LF-only output.
    
    Special rules:
    - 'prohibited' table: only exports 'plmn' and 'taclac' fields
    - All tables: excludes ID fields (columns ending with 'id' or named 'id')

    Query parameters:
    - where: WHERE clause for filtering (optional)
    - order_by: ORDER BY clause (optional)
    - limit: LIMIT clause (optional)
    """
    validate_table_name(table_name)
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        raise ApiError(f"Table {table_name} does not exist", status_code=404, error_type="Validation Error")

    with engine.connect() as connection:
        result = connection.execute(text(f"SHOW COLUMNS FROM `{table_name}`"))
        all_columns = [row[0] for row in result]

    if table_name.lower() == 'prohibited':
        columns_to_export = [col for col in all_columns if col.lower() in ['plmn', 'taclac']]
        if not columns_to_export:
            raise ApiError(
                "Table 'prohibited' does not have 'plmn' and 'taclac' columns",
                status_code=400,
                error_type="Validation Error",
            )
    else:
        columns_to_export = [
            col for col in all_columns
            if not (col.lower() == 'id' or col.lower().endswith('id'))
        ]

        if not columns_to_export:
            raise ApiError(
                f"No exportable columns found in table {table_name}",
                status_code=400,
                error_type="Validation Error",
            )

    logger.info(f"Exporting columns from {table_name}: {columns_to_export}")

    where_clause = request.args.get('where', '')
    order_by = request.args.get('order_by', '')
    limit = request.args.get('limit', '')
    
    columns_string = ', '.join(f'`{col}`' for col in columns_to_export)
    query = f"SELECT {columns_string} FROM `{table_name}`"

    if where_clause:
        query += f" WHERE {where_clause}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit:
        query += f" LIMIT {limit}"

    with engine.connect() as connection:
        result = connection.execute(text(query))
        rows = result.fetchall()

        # Manually build CSV with guaranteed LF-only line endings
        csv_lines = []
        
        # Header row
        header_line = ','.join(columns_to_export)
        csv_lines.append(header_line)
        
        # Data rows
        for row in rows:
            # Convert each cell to string, escape quotes, strip any CR
            escaped_cells = []
            for cell in row:
                if cell is None:
                    escaped_cells.append('')
                else:
                    cell_str = str(cell).replace('\r', '')  # Remove CR
                    # Escape quotes and wrap in quotes if contains comma or quote
                    if ',' in cell_str or '"' in cell_str or '\n' in cell_str:
                        cell_str = '"' + cell_str.replace('"', '""') + '"'
                    escaped_cells.append(cell_str)
            
            row_line = ','.join(escaped_cells)
            csv_lines.append(row_line)
        
        # Join with LF only
        csv_data = '\n'.join(csv_lines) + '\n'
        
        # Absolutely ensure no CR characters
        csv_data = csv_data.replace('\r', '')

    response = Response(
        csv_data,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={table_name}_export.csv'
        }
    )

    logger.info(f"Export successful for table {table_name}. Rows exported: {len(rows)}")
    return response

if __name__ == '__main__':
    app.run(debug=True)
