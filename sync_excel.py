# import os
# import sqlite3
# import pandas as pd

# def sync_data():
#     excel_file = 'AGIexcel.xlsx'
    
#     # Fail-safe check
#     if not os.path.exists(excel_file):
#         print(f"❌ Error: The file '{excel_file}' was not found in the current directory.")
#         return

#     # Read the Excel file
#     df = pd.read_excel(excel_file)
    
#     # Standardize column naming and clean white spaces
#     df.columns = df.columns.str.strip().str.lower()
    
#     # Ensure critical matching variables are treated as clean text strings
#     if 'registration_no' in df.columns:
#         df['registration_no'] = df['registration_no'].astype(str).str.strip()
#     if 'password' in df.columns:
#         df['password'] = df['password'].astype(str).str.strip()

#     # Connect to your app's SQLite database partition
#     conn = sqlite3.connect('AGI.db')
#     cursor = conn.cursor()
    
#     # 1. Ensure the production schema table structure exists perfectly
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS cars (
#             registration_no TEXT PRIMARY KEY,
#             password TEXT,
#             captain_name TEXT,
#             contact1 TEXT,
#             contact2 TEXT,
#             driver_code TEXT,
#             route_name TEXT,
#             ac_status TEXT,
#             car_model TEXT,
#             cnic_front TEXT,
#             cnic_back TEXT,
#             license_front TEXT,
#             license_back TEXT,
#             advance_amount INTEGER DEFAULT 0,
#             advance_timestamp TEXT,
#             day_off_reason TEXT,
#             day_off_timestamp TEXT,
#             actual_fuel_taken REAL DEFAULT 0,
#             custom_rental REAL DEFAULT NULL,
#             custom_kms REAL DEFAULT NULL,
#             custom_working_days REAL DEFAULT NULL
#         )
#     ''')
    
#     print("🔄 Processing spreadsheet records...")
#     success_count = 0

#     # 2. Loop and upsert securely row-by-row
#     for index, row in df.iterrows():
#         reg = row.get('registration_no')
#         if not reg or str(reg).lower() == 'nan':
#             continue  # Skip empty rows safely
            
#         pwd = str(row.get('password', '123'))
#         captain = row.get('captain_name', 'Unknown Captain')
#         route = row.get('route_name', '')
#         model = row.get('car_model', 'Car')
#         c1 = str(row.get('contact1', '')) if 'contact1' in row else ''
#         c2 = str(row.get('contact2', '')) if 'contact2' in row else ''
#         ac = row.get('ac_status', 'No')

#         # This SQL logic updates core baseline variables from Excel, 
#         # but leaves user updates (advance_amount, day_off_reason, fuel logs) completely untouched!
#         cursor.execute("""
#             INSERT INTO cars (registration_no, password, captain_name, route_name, car_model, contact1, contact2, ac_status)
#             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
#             ON CONFLICT(registration_no) DO UPDATE SET
#                 password = excluded.password,
#                 captain_name = excluded.captain_name,
#                 route_name = excluded.route_name,
#                 car_model = excluded.car_model,
#                 contact1 = case when excluded.contact1 != '' then excluded.contact1 else cars.contact1 end,
#                 contact2 = case when excluded.contact2 != '' then excluded.contact2 else cars.contact2 end,
#                 ac_status = excluded.ac_status
#         """, (reg, pwd, captain, route, model, c1, c2, ac))
#         success_count += 1

#     conn.commit()
#     conn.close()
#     print(f"🚀 Successfully synced {success_count} vehicle records without overwriting dynamic app variables!")

# if __name__ == "__main__":
#     sync_data()



import os
import sqlite3
import pandas as pd

def sync_data():
    excel_file = 'ReckittBexcel.xlsx'
    
    if not os.path.exists(excel_file):
        print(f"❌ Error: The file '{excel_file}' was not found in the current directory.")
        return

    # Read the Excel file, stripping whitespace from column names to prevent mapping errors
    df = pd.read_excel(excel_file)
    df.columns = df.columns.str.strip()
    
    # 🚨 Data type normalization to prevent text/numeric cross-matching failures
    string_cols = ['registration_no', 'company_name', 'password', 'captain_name', 
                   'contact1', 'contact2', 'code', 'route_name', 'ac_status', 'car_model']
    for col in string_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace(['-', 'None'], '')

    # Connect directly to the SQLite cluster target
    conn = sqlite3.connect('Reckitt_Benckiser.db')
    cursor = conn.cursor()
    
    # Ensure database schema integrity matches your production model completely
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
            custom_working_days REAL DEFAULT NULL
        )
    ''')
    
    print("🔄 Synchronizing system matrices with master spreadsheet rows...")
    success_count = 0

    for index, row in df.iterrows():
        reg = row.get('registration_no')
        if not reg or str(reg).lower() in ['nan', '']:
            continue  # Skip trailing empty structural rows safely
            
        # Extract row elements using your exact dictionary key names
        params = (
            reg,
            row.get('password', '123456'),
            row.get('captain_name', ''),
            row.get('contact1', ''),
            row.get('contact2', ''),
            row.get('code', ''), # Maps to driver_code in your app
            row.get('route_name', ''),
            row.get('ac_status', 'No'),
            row.get('car_model', 'Car'),
            
            # Numeric customizations
            row.get('custom_rental') if pd.notnull(row.get('custom_rental')) else None,
            row.get('custom_kms') if pd.notnull(row.get('custom_kms')) else None,
            row.get('custom_working_days') if pd.notnull(row.get('custom_working_days')) else None,
            row.get('actual_fuel_taken_by_captian_in_litres') if pd.notnull(row.get('actual_fuel_taken_by_captian_in_litres')) else 0,
            row.get('advance_amount') if pd.notnull(row.get('advance_amount')) else 0,
            
            # Status tracking records
            row.get('advance_timestamp', ''),
            row.get('day_off_reason', ''),
            row.get('day_off_timestamp', '')
        )

        # SQL UPSERT QUERY: Inserts fresh records or updates structural variables 
        # while safely preserving live application data state updates.
        cursor.execute("""
            INSERT INTO cars (
                registration_no, password, captain_name, contact1, contact2, driver_code, 
                route_name, ac_status, car_model, custom_rental, custom_kms, custom_working_days,
                actual_fuel_taken, advance_amount, advance_timestamp, day_off_reason, day_off_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(registration_no) DO UPDATE SET
                password = excluded.password,
                captain_name = excluded.captain_name,
                contact1 = CASE WHEN excluded.contact1 != '' THEN excluded.contact1 ELSE cars.contact1 END,
                contact2 = CASE WHEN excluded.contact2 != '' THEN excluded.contact2 ELSE cars.contact2 END,
                driver_code = excluded.driver_code,
                route_name = excluded.route_name,
                ac_status = excluded.ac_status,
                car_model = excluded.car_model,
                custom_rental = COALESCE(excluded.custom_rental, cars.custom_rental),
                custom_kms = COALESCE(excluded.custom_kms, cars.custom_kms),
                custom_working_days = COALESCE(excluded.custom_working_days, cars.custom_working_days),
                
                -- Dynamic App Data Preservation: Only replaces from Excel if Excel contains active data
                actual_fuel_taken = CASE WHEN excluded.actual_fuel_taken > 0 THEN excluded.actual_fuel_taken ELSE cars.actual_fuel_taken END,
                advance_amount = CASE WHEN excluded.advance_amount > 0 THEN excluded.advance_amount ELSE cars.advance_amount END,
                advance_timestamp = CASE WHEN excluded.advance_timestamp != '' THEN excluded.advance_timestamp ELSE cars.advance_timestamp END,
                day_off_reason = CASE WHEN excluded.day_off_reason != '' THEN excluded.day_off_reason ELSE cars.day_off_reason END,
                day_off_timestamp = CASE WHEN excluded.day_off_timestamp != '' THEN excluded.day_off_timestamp ELSE cars.day_off_timestamp END
        """, params)
        success_count += 1

    conn.commit()
    conn.close()
    print(f"🚀 Master execution complete! Successfully processed {success_count} accounts aligned with your schema columns.")

if __name__ == "__main__":
    sync_data()