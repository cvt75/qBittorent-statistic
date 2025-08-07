import requests
import json
import sqlite3
import time
from datetime import datetime, timedelta

# --- Configuration ---
QBITTORRENT_URL = "http://localhost:8080"
QBITTORRENT_USERNAME = "user"
QBITTORRENT_PASSWORD = "pass"
DB_FILE = "qbittorrent_stats.db" # The name and location of the database file

# API endpoints (remain the same)
LOGIN_URL = f"{QBITTORRENT_URL}/api/v2/auth/login"
SYNC_MAINDATA_URL = f"{QBITTORRENT_URL}/api/v2/sync/maindata"
LOGOUT_URL = f"{QBITTORRENT_URL}/api/v2/auth/logout"

def format_speed(speed_bytes):
    """Converts bytes/second value to a readable format."""
    if speed_bytes < 1024:
        return f"{speed_bytes} B/s"
    elif speed_bytes < 1024 * 1024:
        return f"{speed_bytes / 1024:.2f} KiB/s"
    elif speed_bytes < 1024 * 1024 * 1024:
        return f"{speed_bytes / (1024 * 1024):.2f} MiB/s"
    else:
        return f"{speed_bytes / (1024 * 1024 * 1024):.2f} GiB/s"

def format_size(size_bytes):
    """Converts byte value to a readable format (up to TiB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.2f} KiB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.2f} MiB"
    elif size_bytes < 1024**4:
        return f"{size_bytes / (1024**3):.2f} GiB"
    else:
        return f"{size_bytes / (1024**4):.3f} TiB"

def get_qbittorrent_stats_raw(username, password):
    """Queries the detailed statistics of qBittorrent from the sync/maindata endpoint."""
    session = requests.Session()
    server_state = None
    try:
        login_payload = {'username': username, 'password': password}
        response = session.post(LOGIN_URL, data=login_payload)
        response.raise_for_status()
        if response.text.strip() != "Ok.":
            return None
        params = {'rid': 0}
        response = session.get(SYNC_MAINDATA_URL, params=params)
        response.raise_for_status()
        full_data = response.json()
        server_state = full_data.get('server_state')
        return server_state
    except requests.exceptions.RequestException as e:
        return None
    finally:
        try:
            if 'session' in locals() and session.cookies:
                session.post(LOGOUT_URL)
        except requests.exceptions.RequestException:
            pass

def store_stats_to_db(db_file, stats):
    """Stores statistics in the SQLite database."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS qbittorrent_stats (
            timestamp TEXT PRIMARY KEY,
            alltime_ul INTEGER,
            alltime_dl INTEGER,
            global_ratio REAL,
            total_peer_connections INTEGER,
            read_cache_hits REAL,
            total_buffers_size INTEGER,
            write_cache_overload TEXT,
            read_cache_overload TEXT,
            queued_io_jobs INTEGER,
            average_time_queue REAL,
            total_queued_size INTEGER,
            dl_info_speed INTEGER,
            up_info_speed INTEGER
        )
    ''')
    now = datetime.now().isoformat(timespec='seconds')
    data_to_insert = (
        now,
        stats.get('alltime_ul'),
        stats.get('alltime_dl'),
        stats.get('global_ratio'),
        stats.get('total_peer_connections'),
        stats.get('read_cache_hits'),
        stats.get('total_buffers_size'),
        stats.get('write_cache_overload'),
        stats.get('read_cache_overload'),
        stats.get('queued_io_jobs'),
        stats.get('average_time_queue'),
        stats.get('total_queued_size'),
        stats.get('dl_info_speed'),
        stats.get('up_info_speed')
    )
    try:
        cursor.execute('''
            INSERT INTO qbittorrent_stats (
                timestamp, alltime_ul, alltime_dl, global_ratio, total_peer_connections,
                read_cache_hits, total_buffers_size, write_cache_overload, read_cache_overload,
                queued_io_jobs, average_time_queue, total_queued_size, dl_info_speed, up_info_speed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', data_to_insert)
        conn.commit()
    except sqlite3.IntegrityError:
        pass # Do nothing if an entry already exists
    finally:
        conn.close()

def get_next_target_time(target_minutes, current_time):
    """Calculates the next scheduled run time in the future."""
    
    # Try to find the next run in the current hour
    for tm in target_minutes:
        candidate_time = current_time.replace(minute=tm, second=0, microsecond=0)
        # If the candidate time is in the future, or the current time
        # is very close to the candidate time (e.g., 0-2 seconds away)
        if candidate_time > current_time or \
           (candidate_time.minute == current_time.minute and current_time.second <= 2):
            return candidate_time
            
    # If all target times in the current hour have passed, we take the first target time of the next hour
    next_hour = current_time + timedelta(hours=1)
    return next_hour.replace(minute=target_minutes[0], second=0, microsecond=0)

def main():
    target_minutes = [1, 15, 30, 45, 59] # The minutes when you want to run it
    target_minutes.sort() # Sorting for safety

    while True:
        now_before_sleep = datetime.now()
        
        next_run_time = get_next_target_time(target_minutes, now_before_sleep)
        
        time_to_wait = (next_run_time - now_before_sleep).total_seconds()

        # If time_to_wait <= 0, it means we have already passed the target time.
        # In this case, we immediately "sleep" until the next scheduled time.
        # This prevents it from running again immediately if we are already past the time.
        if time_to_wait <= 0:
            # If we are already past the target time, then in the next cycle
            # the get_next_target_time() function will calculate the next valid time.
            # Here we just take a short sleep to not overload the CPU.
            time.sleep(1) 
            continue # Jump to the beginning of the loop to recalculate the next run
        
        time.sleep(time_to_wait) # Here we sleep until the next scheduled run

        # When we wake up from sleep, CHECK if we are actually in the target minute,
        # and if the time is within the first few seconds of that minute.
        current_time_after_sleep = datetime.now()
        
        # A small tolerance: if the minute matches the target minute, AND the time is between 0-5 seconds
        # OR if the script was delayed, and the timestamp belongs to the previous target minute, but is still at the beginning of the current minute
        if current_time_after_sleep.minute in target_minutes and \
           current_time_after_sleep.second < 5: 
            stats = get_qbittorrent_stats_raw(QBITTORRENT_USERNAME, QBITTORRENT_PASSWORD)
            if stats:
                store_stats_to_db(DB_FILE, stats)

if __name__ == "__main__":
    main()