import psycopg2

def test_connection():
    try:
        conn = psycopg2.connect(
            dbname="wallet_wpmpmp",
            user="wallet_wpmpmp_user",
            password="j9LnormdUlaiWsf36sMTmM79nMXeITRm",
            host="dpg-d2dqf0ripnbc739eva90-a.oregon-postgres.render.com",
            port="5432"
        )

        cur = conn.cursor()
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        print("✅ اتصال موفق! نسخه دیتابیس:", db_version[0])

        cur.close()
        conn.close()

    except Exception as e:
        print("❌ خطا در اتصال:", e)

if __name__ == "__main__":
    test_connection()
