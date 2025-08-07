from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
from datetime import datetime, timedelta
import calendar
from dateutil.relativedelta import relativedelta
import re

app = Flask(__name__)
DB_FILE = "qbittorrent_stats.db"
NCORE_DB_FILE = "ncore_full.db"  # Új adatbázis fájl neve

def get_daily_totals(db_file):
    """Lekérdezi a napi összesített feltöltött és letöltött adatmennyiség növekményét GB-ban."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''
        WITH DailyData AS (
            SELECT
                DATE(timestamp) AS dt,
                timestamp,
                alltime_ul,
                alltime_dl
            FROM qbittorrent_stats
        ),
        RankedData AS (
            SELECT
                dt,
                timestamp,
                alltime_ul,
                alltime_dl,
                ROW_NUMBER() OVER (PARTITION BY dt ORDER BY timestamp ASC) AS rn_asc,
                ROW_NUMBER() OVER (PARTITION BY dt ORDER BY timestamp DESC) AS rn_desc
            FROM DailyData
        )
        SELECT
            rd_first.dt,
            (rd_last.alltime_ul - rd_first.alltime_ul) / (1024 * 1024 * 1024.0) AS daily_ul_gb,
            (rd_last.alltime_dl - rd_first.alltime_dl) / (1024 * 1024 * 1024.0) AS daily_dl_gb
        FROM RankedData rd_first
        JOIN RankedData rd_last ON rd_first.dt = rd_last.dt AND rd_first.rn_asc = 1 AND rd_last.rn_desc = 1
        ORDER BY rd_first.dt DESC;
    ''')
    daily_totals = cursor.fetchall()
    conn.close()
    return daily_totals

def get_hourly_stats(db_file):
    """Lekérdezi a legutóbbi 24 óra óránkénti összesített feltöltött és letöltött adatmennyiség növekményét GB-ban."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    now = datetime.now()
    past_24_hours = now - timedelta(hours=24)
    cursor.execute('''
        WITH HourlyData AS (
            SELECT
                strftime('%Y-%m-%d %H:00', timestamp) AS hour,
                timestamp,
                alltime_ul,
                alltime_dl
            FROM qbittorrent_stats
            WHERE timestamp >= ? AND timestamp <= ?
        ),
        RankedHourlyData AS (
            SELECT
                hour,
                timestamp,
                alltime_ul,
                alltime_dl,
                ROW_NUMBER() OVER (PARTITION BY hour ORDER BY timestamp ASC) AS rn_asc,
                ROW_NUMBER() OVER (PARTITION BY hour ORDER BY timestamp DESC) AS rn_desc
            FROM HourlyData
        )
        SELECT
            rhd_first.hour,
            (rhd_last.alltime_ul - rhd_first.alltime_ul) / (1024.0 * 1024 * 1024) AS hourly_ul_gb,
            (rhd_last.alltime_dl - rhd_first.alltime_dl) / (1024.0 * 1024 * 1024) AS hourly_dl_gb
        FROM RankedHourlyData rhd_first
        JOIN RankedHourlyData rhd_last ON rhd_first.hour = rhd_last.hour AND rhd_first.rn_asc = 1 AND rhd_last.rn_desc = 1
        ORDER BY rhd_first.hour;
    ''', (past_24_hours.isoformat(), now.isoformat()))
    hourly_stats = cursor.fetchall()
    conn.close()
    return hourly_stats

def get_latest_stats(db_file):
    """Lekérdezi a legfrissebb adatbázis rekordot."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            timestamp,
            alltime_ul,
            alltime_dl,
            global_ratio
        FROM qbittorrent_stats
        ORDER BY timestamp DESC
        LIMIT 1;
    ''')
    latest_data = cursor.fetchone()
    conn.close()
    if latest_data:
        timestamp_str = latest_data[0]
        timestamp_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        formatted_timestamp = timestamp_dt.strftime('%Y-%m-%d %H:%M:%S')

        alltime_ul_gb = latest_data[1] / (1024.0 * 1024 * 1024)
        alltime_dl_gb = latest_data[2] / (1024.0 * 1024 * 1024)
        global_ratio_str = latest_data[3]
        global_ratio = None
        if global_ratio_str is not None:
            global_ratio_str = global_ratio_str.replace(',', '.')
            try:
                global_ratio = float(global_ratio_str)
            except ValueError:
                print(f"Hiba a globális arány konvertálásakor: {global_ratio_str}")
                global_ratio = None

        if alltime_ul_gb >= 1200:
            alltime_ul_tib = alltime_ul_gb / 1024
            alltime_ul_str = f"{alltime_ul_tib:.3f} TiB"
        else:
            alltime_ul_str = f"{alltime_ul_gb:.3f} GB"

        if alltime_dl_gb >= 1200:
            alltime_dl_tib = alltime_dl_gb / 1024
            alltime_dl_str = f"{alltime_dl_tib:.3f} TiB"
        else:
            alltime_dl_str = f"{alltime_dl_gb:.3f} GB"

        return (formatted_timestamp, alltime_ul_str, alltime_dl_str, f"{global_ratio:.2f}" if global_ratio is not None else "N/A")
    return None

def get_weekly_stats(db_file, num_weeks=5):
    """Lekérdezi az utolsó num_weeks hét feltöltési és letöltési statisztikáit (hétfőtől vasárnapig)
    figyelembe véve az óra, perc, másodperc értékeket is."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    weekly_data = []
    now = datetime.now()
    for i in range(num_weeks):
        # Számoljuk ki a hétfő 00:00:00 dátumát
        monday = now - timedelta(days=now.weekday() + 7 * i)
        monday_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        # Számoljuk ki a vasárnap 23:59:59 dátumát
        sunday = monday + timedelta(days=6)
        sunday_end = sunday.replace(hour=23, minute=59, second=59, microsecond=999999)
        year_week = monday.isocalendar()[1]
        #         print(f"Hét {year_week}: Hétfő: {monday_start.strftime('%Y-%m-%d %H:%M:%S')}, Vasárnap: {sunday_end.strftime('%Y-%m-%d %H:%M:%S')}")

        cursor.execute('''
            SELECT timestamp, alltime_ul, alltime_dl
            FROM qbittorrent_stats
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT 1
        ''', (monday_start.isoformat(), sunday_end.isoformat()))
        first_entry = cursor.fetchone()

        cursor.execute('''
            SELECT timestamp, alltime_ul, alltime_dl
            FROM qbittorrent_stats
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (monday_start.isoformat(), sunday_end.isoformat()))
        last_entry = cursor.fetchone()

        if first_entry and last_entry:
            weekly_ul = (last_entry[1] - first_entry[1]) / (1024 * 1024 * 1024.0)
            weekly_dl = (last_entry[2] - first_entry[2]) / (1024 * 1024 * 1024.0)
            weekly_data.append({'week': year_week, 'upload': max(0, weekly_ul), 'download': max(0, weekly_dl)})
        else:
            weekly_data.append({'week': year_week, 'upload': 0, 'download': 0})

    conn.close()
    return weekly_data

from dateutil.relativedelta import relativedelta

def get_monthly_stats(db_file, num_months=6):
    """Lekérdezi az utolsó num_months havi feltöltési és letöltési statisztikáit."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    monthly_data = []
    now = datetime.now()
    for i in range(num_months):
        # Számoljuk ki a hónap első napját
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=i)
        # Számoljuk ki a hónap utolsó napját
        next_month = first_day_of_month + relativedelta(months=1)
        last_day_of_month = next_month - timedelta(days=1)

        print(f"Hónap: {first_day_of_month.strftime('%Y-%m-%d')} - {last_day_of_month.strftime('%Y-%m-%d')}")

        cursor.execute('''
            SELECT timestamp, alltime_ul, alltime_dl
            FROM qbittorrent_stats
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT 1
        ''', (first_day_of_month.isoformat(), last_day_of_month.isoformat()))
        first_entry = cursor.fetchone()

        cursor.execute('''
            SELECT timestamp, alltime_ul, alltime_dl
            FROM qbittorrent_stats
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (first_day_of_month.isoformat(), last_day_of_month.isoformat()))
        last_entry = cursor.fetchone()

        if first_entry and last_entry:
            monthly_ul = (last_entry[1] - first_entry[1]) / (1024 * 1024 * 1024.0)
            monthly_dl = (last_entry[2] - first_entry[2]) / (1024 * 1024 * 1024.0)
            monthly_data.append({'year': first_day_of_month.year, 'month': first_day_of_month.month, 'upload': max(0, monthly_ul), 'download': max(0, monthly_dl)})
        else:
            monthly_data.append({'year': first_day_of_month.year, 'month': first_day_of_month.month, 'upload': 0, 'download': 0})

    conn.close()
    
    return monthly_data

@app.route("/")
def index():
    daily_data = get_daily_totals(DB_FILE)
    hourly_data = get_hourly_stats(DB_FILE)
    latest_stats = get_latest_stats(DB_FILE)
    return render_template("index.html", daily_data=daily_data, hourly_data=hourly_data, latest_stats=latest_stats)

@app.route("/detailed_stats")
def detailed_stats():
    weekly_stats = get_weekly_stats(DB_FILE)
    weekly_stats = weekly_stats[::-1]  # Próbáljuk meg ezzel a módszerrel megfordítani a listát
    monthly_stats = get_monthly_stats(DB_FILE)
    return render_template("detailed_stats.html", weekly_stats=weekly_stats, monthly_stats=monthly_stats)


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)