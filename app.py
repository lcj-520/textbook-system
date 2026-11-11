# -*- coding: utf-8 -*-
"""教材采购管理系统 —— 云端版（SQLite，供公网演示使用）

与本地版区别：
1. 数据库由 MySQL 换为 SQLite，无需额外数据库服务，部署到 Render 即可直接用；
2. 触发器（采购自动加库存）与存储过程（单人退订）的逻辑改由 Python 在事务中实现，功能一致；
3. 页面由 Flask 直接托管，浏览器访问站点根路径即为登录页；
4. 登录账号密码从环境变量读取（APP_USER / APP_PASSWORD），不在代码中保存明文。
"""
import os
import sqlite3

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DB_PATH') or os.path.join(BASE_DIR, 'textbook.db')

APP_USER = os.environ.get('APP_USER', 'lcj')
APP_PASSWORD = os.environ.get('APP_PASSWORD', '')

app = Flask(__name__)
app.json.ensure_ascii = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS t_supplier (
    supplier_id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name TEXT
);
CREATE TABLE IF NOT EXISTS t_book (
    book_id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_name TEXT,
    supplier_id INTEGER REFERENCES t_supplier(supplier_id),
    price REAL,
    stock INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS t_dept (
    dept_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dept_name TEXT
);
CREATE TABLE IF NOT EXISTS t_department (
    department_id INTEGER PRIMARY KEY AUTOINCREMENT,
    department_name TEXT,
    dept_id INTEGER REFERENCES t_dept(dept_id)
);
CREATE TABLE IF NOT EXISTS t_major (
    major_id INTEGER PRIMARY KEY AUTOINCREMENT,
    major_name TEXT,
    department_id INTEGER REFERENCES t_department(department_id)
);
CREATE TABLE IF NOT EXISTS t_class (
    class_id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT,
    major_id INTEGER REFERENCES t_major(major_id)
);
CREATE TABLE IF NOT EXISTS t_student (
    student_no TEXT PRIMARY KEY,
    student_name TEXT,
    class_id INTEGER REFERENCES t_class(class_id)
);
CREATE TABLE IF NOT EXISTS t_order (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER REFERENCES t_class(class_id),
    book_id INTEGER REFERENCES t_book(book_id),
    order_qty INTEGER,
    status TEXT DEFAULT '待采购'
);
CREATE TABLE IF NOT EXISTS t_purchase (
    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES t_book(book_id),
    purchase_qty INTEGER,
    purchase_date TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS t_issue (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES t_order(order_id),
    student_no TEXT REFERENCES t_student(student_no),
    issue_qty INTEGER,
    issue_date TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS t_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    detail TEXT,
    log_time TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """建表并在首次启动时写入演示数据"""
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    empty = conn.execute("SELECT COUNT(*) FROM t_book").fetchone()[0] == 0
    seed_file = os.path.join(BASE_DIR, 'seed.sql')
    if empty and os.path.exists(seed_file):
        with open(seed_file, encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()
    conn.close()


def add_log(cursor, action, detail):
    cursor.execute("INSERT INTO t_log (action, detail) VALUES (?, ?)", (action, (detail or '')[:255]))


def resolve_class_ids(cursor, data):
    """按 学院/系/专业/班级 条件解析班级ID，未选层级代表全部"""
    sql = """
        SELECT c.class_id FROM t_class c
        JOIN t_major m ON c.major_id = m.major_id
        JOIN t_department dep ON m.department_id = dep.department_id
        JOIN t_dept d ON dep.dept_id = d.dept_id
        WHERE 1=1
    """
    params = []
    if data.get('dept_id'):
        sql += " AND d.dept_id = ?"; params.append(data['dept_id'])
    if data.get('department_id'):
        sql += " AND dep.department_id = ?"; params.append(data['department_id'])
    if data.get('major_id'):
        sql += " AND m.major_id = ?"; params.append(data['major_id'])
    if data.get('class_id'):
        sql += " AND c.class_id = ?"; params.append(data['class_id'])
    cursor.execute(sql, params)
    return [row['class_id'] for row in cursor.fetchall()]


def rows_to_dict(rows):
    return [dict(r) for r in rows]


@app.route('/')
def index_page():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    if not APP_PASSWORD:
        return jsonify({"code": 500, "msg": "服务端未配置登录密码（环境变量 APP_PASSWORD）"})
    if data.get('username') == APP_USER and data.get('password') == APP_PASSWORD:
        return jsonify({"code": 200})
    return jsonify({"code": 401, "msg": "账号或密码错误"})


@app.route('/api/books', methods=['GET'])
def get_books():
    conn = get_db()
    rows = conn.execute("""
        SELECT b.*, s.supplier_name
        FROM t_book b LEFT JOIN t_supplier s ON b.supplier_id = s.supplier_id
        ORDER BY b.book_id ASC
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM t_supplier").fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/add_supplier', methods=['POST'])
def add_supplier():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO t_supplier (supplier_name) VALUES (?)", (data.get('supplier_name'),))
        add_log(cur, '新增供应商', f"供应商ID:{cur.lastrowid} {data.get('supplier_name')}")
        conn.commit()
        return jsonify({"code": 200, "msg": "供应商创建成功！"})
    except Exception:
        conn.rollback()
        return jsonify({"code": 500, "msg": "创建失败"})
    finally:
        conn.close()


@app.route('/api/hierarchy', methods=['GET'])
def get_hierarchy():
    conn = get_db()
    rows = conn.execute("""
        SELECT d.dept_id, d.dept_name,
               dep.department_id, dep.department_name,
               m.major_id, m.major_name,
               c.class_id, c.class_name
        FROM t_class c
        JOIN t_major m ON c.major_id = m.major_id
        JOIN t_department dep ON m.department_id = dep.department_id
        JOIN t_dept d ON dep.dept_id = d.dept_id
        ORDER BY d.dept_id, dep.department_id, m.major_id, c.class_id
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/add_book', methods=['POST'])
def add_book():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO t_book (book_name, supplier_id, price, stock) VALUES (?, ?, ?, ?)",
                    (data.get('book_name'), data.get('supplier_id'), data.get('price'), data.get('stock')))
        book_id = cur.lastrowid
        cur.execute("SELECT supplier_name FROM t_supplier WHERE supplier_id = ?", (data.get('supplier_id'),))
        supplier = cur.fetchone()
        supplier_name = supplier['supplier_name'] if supplier else data.get('supplier_id')
        add_log(cur, '新增教材', f"教材ID:{book_id} 《{data.get('book_name')}》 供应商:{supplier_name} 单价:¥{data.get('price')} 初始库存:{data.get('stock')}")
        conn.commit()
        return jsonify({"code": 200, "msg": "教材新增成功"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"新增失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/update_book', methods=['POST'])
def update_book():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT book_name, price FROM t_book WHERE book_id = ?", (data.get('book_id'),))
        book = cur.fetchone()
        cur.execute("UPDATE t_book SET price = ? WHERE book_id = ?", (data.get('price'), data.get('book_id')))
        if book:
            add_log(cur, '修改价格', f"教材ID:{data.get('book_id')} 《{book['book_name']}》 ¥{book['price']} → ¥{data.get('price')}")
        conn.commit()
        return jsonify({"code": 200, "msg": "教材修改成功"})
    except Exception:
        conn.rollback()
        return jsonify({"code": 500, "msg": "修改失败"})
    finally:
        conn.close()


@app.route('/api/delete_book/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT book_name FROM t_book WHERE book_id = ?", (book_id,))
        book = cur.fetchone()
        cur.execute("SELECT order_id FROM t_order WHERE book_id = ?", (book_id,))
        order_ids = [row['order_id'] for row in cur.fetchall()]
        if order_ids:
            placeholders = ','.join(['?'] * len(order_ids))
            cur.execute(f"DELETE FROM t_issue WHERE order_id IN ({placeholders})", order_ids)
        cur.execute("DELETE FROM t_order WHERE book_id = ?", (book_id,))
        cur.execute("DELETE FROM t_purchase WHERE book_id = ?", (book_id,))
        cur.execute("DELETE FROM t_book WHERE book_id = ?", (book_id,))
        add_log(cur, '删除教材', f"教材ID:{book_id} 《{book['book_name'] if book else '未知'}》 及关联征订/发放/采购记录")
        conn.commit()
        return jsonify({"code": 200, "msg": "删除成功"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"删除失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/add_order', methods=['POST'])
def add_order():
    data = request.get_json(silent=True) or {}
    book_id = data.get('book_id')
    order_qty = int(data.get('order_qty') or 0)
    raw_ids = data.get('class_ids') or ([data.get('class_id')] if data.get('class_id') else [])
    class_ids = list(dict.fromkeys(int(cid) for cid in raw_ids if cid not in (None, '')))

    if not book_id:
        return jsonify({"code": 400, "msg": "请选择教材"})
    if order_qty <= 0:
        return jsonify({"code": 400, "msg": "征订数量必须大于 0"})
    if not class_ids:
        return jsonify({"code": 400, "msg": "请至少选择到系或班级"})

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT book_name FROM t_book WHERE book_id = ?", (book_id,))
        book = cur.fetchone()
        if not book:
            return jsonify({"code": 404, "msg": "教材不存在"})

        placeholders = ','.join(['?'] * len(class_ids))
        cur.execute(f"SELECT class_id, class_name FROM t_class WHERE class_id IN ({placeholders}) ORDER BY class_id", class_ids)
        classes = cur.fetchall()
        if not classes:
            return jsonify({"code": 404, "msg": "所选范围内没有可征订的班级"})

        cur.executemany("INSERT INTO t_order (class_id, book_id, order_qty) VALUES (?, ?, ?)",
                        [(c['class_id'], book_id, order_qty) for c in classes])
        names = '、'.join(c['class_name'] for c in classes)
        add_log(cur, '教材征订', f"《{book['book_name']}》 {order_qty}本/班 共{len(classes)}个班：{names}")
        conn.commit()
        return jsonify({"code": 200, "msg": f"征订提交成功，共 {len(classes)} 个班级"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"征订失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/query_orders', methods=['POST'])
def query_orders():
    data = request.get_json(silent=True) or {}
    class_id, status, book_name = data.get('class_id'), data.get('status'), data.get('book_name')
    conn = get_db()
    sql = """
        SELECT d.dept_name AS 学院, dep.department_name AS 系, m.major_name AS 专业, c.class_name AS 班级,
               b.book_name AS 教材, o.order_qty AS 数量, (o.order_qty * b.price) AS 总金额, o.status AS 状态
        FROM t_order o
        JOIN t_class c ON o.class_id = c.class_id
        JOIN t_major m ON c.major_id = m.major_id
        JOIN t_department dep ON m.department_id = dep.department_id
        JOIN t_dept d ON dep.dept_id = d.dept_id
        JOIN t_book b ON o.book_id = b.book_id
        WHERE 1=1
    """
    params = []
    if class_id:
        sql += " AND c.class_id = ?"; params.append(class_id)
    if status:
        sql += " AND o.status = ?"; params.append(status)
    if book_name:
        sql += " AND b.book_name LIKE ?"; params.append(f"%{book_name}%")
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/purchase', methods=['POST'])
def purchase():
    data = request.get_json(silent=True) or {}
    book_id = data.get('book_id')
    qty = int(data.get('qty') or 0)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT book_name, price FROM t_book WHERE book_id = ?", (book_id,))
        book = cur.fetchone()
        cur.execute("INSERT INTO t_purchase (book_id, purchase_qty) VALUES (?, ?)", (book_id, qty))
        # 原 MySQL 触发器 trg_purchase_add_stock 的逻辑
        cur.execute("UPDATE t_book SET stock = stock + ? WHERE book_id = ?", (qty, book_id))
        if book:
            add_log(cur, '采购入库', f"《{book['book_name']}》 {qty}本 单价:¥{book['price']} 总额:¥{float(book['price']) * qty:.2f}")
        else:
            add_log(cur, '采购入库', f"教材ID:{book_id} {qty}本")
        conn.commit()
        return jsonify({"code": 200, "msg": "验收入库成功，库存已更新"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"入库失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/purchase_records', methods=['GET'])
def get_purchase_records():
    conn = get_db()
    rows = conn.execute("""
        SELECT p.purchase_id AS 流水号, s.supplier_name AS 供应商, b.book_name AS 教材,
               b.price AS 单价, p.purchase_qty AS 采购数量,
               (p.purchase_qty * b.price) AS 采购总额, p.purchase_date AS 入库时间
        FROM t_purchase p
        JOIN t_book b ON p.book_id = b.book_id
        JOIN t_supplier s ON b.supplier_id = s.supplier_id
        ORDER BY p.purchase_date DESC
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/warning', methods=['GET'])
def get_warning():
    conn = get_db()
    rows = conn.execute("SELECT book_name, stock FROM t_book WHERE stock < 50").fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/issue', methods=['POST'])
def do_issue():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO t_issue (order_id, student_no, issue_qty) VALUES (?, ?, ?)",
                    (data.get('order_id'), data.get('student_no'), data.get('qty')))
        add_log(cur, '教材发放', f"订单号{data.get('order_id')} 学号{data.get('student_no')} 数量{data.get('qty')}")
        conn.commit()
        return jsonify({"code": 200, "msg": "教材发放登记成功"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"发放失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/batch_issue', methods=['POST'])
def batch_issue():
    """按 学院/系/专业/班级 范围，给范围内所有未领取该教材的学生批量发放"""
    data = request.get_json(silent=True) or {}
    book_id = data.get('book_id')
    qty = int(data.get('qty') or 1)
    if not book_id:
        return jsonify({"code": 400, "msg": "请选择教材"})
    if qty <= 0:
        return jsonify({"code": 400, "msg": "发放数量必须大于 0"})

    conn = get_db()
    try:
        cur = conn.cursor()
        class_ids = resolve_class_ids(cur, data)
        if not class_ids:
            return jsonify({"code": 404, "msg": "所选范围内没有班级"})

        cur.execute("SELECT book_name FROM t_book WHERE book_id = ?", (book_id,))
        book = cur.fetchone()
        if not book:
            return jsonify({"code": 404, "msg": "教材不存在"})

        placeholders = ','.join(['?'] * len(class_ids))
        cur.execute(f"""
            SELECT o.order_id, o.class_id, c.class_name
            FROM t_order o JOIN t_class c ON o.class_id = c.class_id
            WHERE o.book_id = ? AND o.class_id IN ({placeholders}) AND o.status <> '已退订'
            ORDER BY c.class_id
        """, [book_id, *class_ids])
        orders = cur.fetchall()

        total, touched = 0, []
        for order in orders:
            cur.execute("""
                SELECT s.student_no FROM t_student s
                WHERE s.class_id = ?
                  AND s.student_no NOT IN (SELECT i.student_no FROM t_issue i WHERE i.order_id = ?)
            """, (order['class_id'], order['order_id']))
            students = cur.fetchall()
            if not students:
                continue
            cur.executemany("INSERT INTO t_issue (order_id, student_no, issue_qty) VALUES (?, ?, ?)",
                            [(order['order_id'], s['student_no'], qty) for s in students])
            total += len(students)
            touched.append(f"{order['class_name']}({len(students)}人)")

        if total == 0:
            return jsonify({"code": 400, "msg": "所选范围内该教材已全部发放完毕，或无有效征订记录"})

        add_log(cur, '批量发放', f"《{book['book_name']}》 共{total}人 {qty}本/人：{'、'.join(touched)}")
        conn.commit()
        return jsonify({"code": 200, "msg": f"批量发放成功，共 {len(touched)} 个班级 / {total} 名学生"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"批量发放失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/unissued', methods=['POST'])
def get_unissued():
    data = request.get_json(silent=True) or {}
    class_id, book_id = data.get('class_id'), data.get('book_id')
    conn = get_db()
    sql = """
        SELECT c.class_name AS 班级, b.book_name AS 教材, s.student_no AS 学号, s.student_name AS 姓名
        FROM t_student s
        JOIN t_class c ON s.class_id = c.class_id
        JOIN t_order o ON o.class_id = c.class_id
        JOIN t_book b ON o.book_id = b.book_id
        WHERE s.student_no NOT IN (
            SELECT i.student_no FROM t_issue i WHERE i.order_id = o.order_id
        )
    """
    params = []
    if class_id:
        sql += " AND c.class_id = ?"; params.append(class_id)
    if book_id:
        sql += " AND b.book_id = ?"; params.append(book_id)
    sql += " ORDER BY c.class_name, b.book_name, s.student_no"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/return', methods=['POST'])
def return_book():
    """原 MySQL 存储过程 sp_return_book 的逻辑：单人退订，事务内完成"""
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT book_id FROM t_order WHERE order_id = ?", (data.get('order_id'),))
        order = cur.fetchone()
        if not order:
            return jsonify({"code": 404, "msg": "订单不存在"})
        cur.execute("UPDATE t_book SET stock = stock + ? WHERE book_id = ?", (data.get('qty'), order['book_id']))
        cur.execute("UPDATE t_order SET status = '已退订' WHERE order_id = ?", (data.get('order_id'),))
        add_log(cur, '退教材', f"订单号{data.get('order_id')} 学号{data.get('student_no')} 数量{data.get('qty')}")
        conn.commit()
        return jsonify({"code": 200, "msg": "单人退货成功，事务已提交"})
    except Exception:
        conn.rollback()
        return jsonify({"code": 500, "msg": "退货失败，事务已回滚"})
    finally:
        conn.close()


@app.route('/api/batch_return', methods=['POST'])
def batch_return():
    """按 学院/系/专业/班级 范围，对范围内所有班级的该教材订单各退 qty 本"""
    data = request.get_json(silent=True) or {}
    book_id = data.get('book_id')
    return_qty = int(data.get('qty') or 0)
    if not book_id:
        return jsonify({"code": 400, "msg": "请选择教材"})
    if return_qty <= 0:
        return jsonify({"code": 400, "msg": "退订数量必须大于0"})

    conn = get_db()
    try:
        cur = conn.cursor()
        class_ids = resolve_class_ids(cur, data)
        if not class_ids:
            return jsonify({"code": 404, "msg": "所选范围内没有班级"})

        placeholders = ','.join(['?'] * len(class_ids))
        cur.execute(f"""
            SELECT o.order_id, o.class_id, o.order_qty, o.status, c.class_name
            FROM t_order o JOIN t_class c ON o.class_id = c.class_id
            WHERE o.book_id = ? AND o.class_id IN ({placeholders})
            ORDER BY c.class_id
        """, [book_id, *class_ids])
        orders = cur.fetchall()
        if not orders:
            return jsonify({"code": 404, "msg": "所选范围内没有该教材的征订记录"})

        cur.execute("SELECT book_name FROM t_book WHERE book_id = ?", (book_id,))
        book = cur.fetchone()

        total, touched = 0, []
        for order in orders:
            remain = int(order['order_qty'] or 0)
            if order['status'] == '已退订' or remain <= 0:
                continue
            actual = min(return_qty, remain)
            new_qty = remain - actual
            new_status = '已退订' if new_qty == 0 else '待采购'
            cur.execute("UPDATE t_order SET order_qty = ?, status = ? WHERE order_id = ?",
                        (new_qty, new_status, order['order_id']))
            total += actual
            touched.append(f"{order['class_name']}({actual}本)")

        if total == 0:
            return jsonify({"code": 400, "msg": "所选范围内的征订记录均已全部退订"})

        cur.execute("UPDATE t_book SET stock = stock + ? WHERE book_id = ?", (total, book_id))
        add_log(cur, '批量退订', f"《{book['book_name'] if book else book_id}》 {len(touched)}个班 共退{total}本：{'、'.join(touched)}")
        conn.commit()
        return jsonify({"code": 200, "msg": f"成功退订 {total} 本（{len(touched)} 个班级），库存已回写！"})
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "msg": f"退订失败: {str(e)}"})
    finally:
        conn.close()


@app.route('/api/supplier_stats', methods=['GET'])
def supplier_stats():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.supplier_name AS 供应商, SUM(p.purchase_qty) AS 供货数量,
               SUM(p.purchase_qty * b.price) AS 结算总额
        FROM t_purchase p JOIN t_book b ON p.book_id = b.book_id
        JOIN t_supplier s ON b.supplier_id = s.supplier_id
        GROUP BY s.supplier_name
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/academy_stats', methods=['GET'])
def academy_stats():
    conn = get_db()
    rows = conn.execute("""
        SELECT d.dept_name AS 学院, SUM(o.order_qty * b.price) AS 消耗经费
        FROM t_order o JOIN t_class c ON o.class_id = c.class_id
        JOIN t_major m ON c.major_id = m.major_id
        JOIN t_department dep ON m.department_id = dep.department_id
        JOIN t_dept d ON dep.dept_id = d.dept_id
        JOIN t_book b ON o.book_id = b.book_id
        GROUP BY d.dept_name
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


@app.route('/api/logs', methods=['GET'])
def get_logs():
    conn = get_db()
    rows = conn.execute("""
        SELECT log_time AS 时间, action AS 动作, detail AS 详情
        FROM t_log ORDER BY log_time DESC, log_id DESC LIMIT 10
    """).fetchall()
    conn.close()
    return jsonify({"code": 200, "data": rows_to_dict(rows)})


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
