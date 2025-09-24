import mysql.connector
from werkzeug.security import generate_password_hash
from config import Config

def get_connection():
    return mysql.connector.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME
    )

def init_db():
    conn = get_connection()
    c = conn.cursor()

    # mensajes
    c.execute("""
    CREATE TABLE IF NOT EXISTS mensajes (
      id INT AUTO_INCREMENT PRIMARY KEY,
      wa_id VARCHAR(255),
      reply_to_wa_id VARCHAR(255),
      numero     VARCHAR(20),
      mensaje    TEXT,
      tipo       VARCHAR(50),
      media_id   VARCHAR(255),
      media_url  TEXT,
      mime_type  TEXT,
      link_url   TEXT,
      link_title TEXT,
      link_body  TEXT,
      link_thumb TEXT,
      step       TEXT,
      regla_id   INT,
      timestamp  DATETIME
    ) ENGINE=InnoDB;
    """)

    # Migración defensiva de columnas link_*
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'link_url';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN link_url TEXT NULL;")
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'link_title';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN link_title TEXT NULL;")
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'link_body';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN link_body TEXT NULL;")
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'link_thumb';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN link_thumb TEXT NULL;")

    # Migración defensiva de columnas wa_id y reply_to_wa_id
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'wa_id';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN wa_id VARCHAR(255) NULL;")
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'reply_to_wa_id';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN reply_to_wa_id VARCHAR(255) NULL;")

    # Migración defensiva de columnas step y regla_id
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'step';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN step TEXT NULL;")
    c.execute("SHOW COLUMNS FROM mensajes LIKE 'regla_id';")
    if not c.fetchone():
        c.execute("ALTER TABLE mensajes ADD COLUMN regla_id INT NULL;")

    # Índice sobre timestamp para mejorar el ordenamiento cronológico
    c.execute("SHOW INDEX FROM mensajes WHERE Key_name = 'idx_mensajes_timestamp';")
    if not c.fetchone():
        c.execute("CREATE INDEX idx_mensajes_timestamp ON mensajes (timestamp);")

    # mensajes procesados
    c.execute("""
    CREATE TABLE IF NOT EXISTS mensajes_procesados (
      mensaje_id VARCHAR(255) PRIMARY KEY
    ) ENGINE=InnoDB;
    """)

    # usuarios
    c.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
      id INT AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(50) UNIQUE NOT NULL,
      password VARCHAR(128) NOT NULL
    ) ENGINE=InnoDB;
    """)

    # Ampliar password para soportar hashes de Werkzeug
    c.execute("SHOW COLUMNS FROM usuarios LIKE 'password';")
    col = c.fetchone()
    # col -> (Field, Type, Null, Key, Default, Extra)
    if col and isinstance(col[1], str) and 'varchar(128)' in col[1].lower():
        c.execute("ALTER TABLE usuarios MODIFY password VARCHAR(255) NOT NULL;")

    # roles
    c.execute("""
    CREATE TABLE IF NOT EXISTS roles (
      id INT AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(50) NOT NULL,
      keyword VARCHAR(20) UNIQUE NOT NULL
    ) ENGINE=InnoDB;
    """)

    # user_roles (pivote con FKs)
    c.execute("""
    CREATE TABLE IF NOT EXISTS user_roles (
      user_id INT NOT NULL,
      role_id INT NOT NULL,
      PRIMARY KEY (user_id, role_id),
      FOREIGN KEY (user_id) REFERENCES usuarios(id) ON DELETE CASCADE,
      FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """)

    # Migración: si existe usuarios.rol => poblar roles/user_roles y DROP columna
    c.execute("SHOW COLUMNS FROM usuarios LIKE 'rol';")
    if c.fetchone():
        c.execute("SELECT DISTINCT rol FROM usuarios;")
        for (rol,) in c.fetchall():
            if not rol:
                continue
            c.execute("""
                INSERT INTO roles (name, keyword)
                SELECT %s, %s FROM DUAL
                WHERE NOT EXISTS (SELECT 1 FROM roles WHERE keyword=%s)
            """, (rol.capitalize(), rol, rol))

        c.execute("SELECT id, rol FROM usuarios;")
        for user_id, rol in c.fetchall():
            if not rol:
                continue
            c.execute("SELECT id FROM roles WHERE keyword=%s", (rol,))
            row = c.fetchone()
            if row:
                role_id = row[0]
                c.execute(
                    "INSERT IGNORE INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                    (user_id, role_id)
                )

        c.execute("ALTER TABLE usuarios DROP COLUMN rol;")

    # reglas (incluye rol_keyword alineado a roles.keyword)
    c.execute("""
    CREATE TABLE IF NOT EXISTS reglas (
      id INT AUTO_INCREMENT PRIMARY KEY,
      step TEXT NOT NULL,
      input_text TEXT NOT NULL,
      respuesta TEXT NOT NULL,
      siguiente_step TEXT,
      tipo VARCHAR(20) NOT NULL DEFAULT 'texto',
      opciones TEXT,
      rol_keyword VARCHAR(20) NULL,
      calculo TEXT,
      handler VARCHAR(50),
      media_url TEXT,
      media_tipo VARCHAR(20)
    ) ENGINE=InnoDB;
    """)

    # Migración defensiva de columnas calculo, handler y medios
    c.execute("SHOW COLUMNS FROM reglas LIKE 'calculo';")
    if not c.fetchone():
        c.execute("ALTER TABLE reglas ADD COLUMN calculo TEXT NULL;")
    c.execute("SHOW COLUMNS FROM reglas LIKE 'handler';")
    if not c.fetchone():
        c.execute("ALTER TABLE reglas ADD COLUMN handler VARCHAR(50) NULL;")
    c.execute("SHOW COLUMNS FROM reglas LIKE 'media_url';")
    if not c.fetchone():
        c.execute("ALTER TABLE reglas ADD COLUMN media_url TEXT NULL;")
    c.execute("SHOW COLUMNS FROM reglas LIKE 'media_tipo';")
    if not c.fetchone():
        c.execute("ALTER TABLE reglas ADD COLUMN media_tipo VARCHAR(20) NULL;")

    # regla_medias: soporta múltiples archivos por regla
    c.execute("""
    CREATE TABLE IF NOT EXISTS regla_medias (
      id INT AUTO_INCREMENT PRIMARY KEY,
      regla_id INT NOT NULL,
      media_url TEXT NOT NULL,
      media_tipo VARCHAR(20),
      FOREIGN KEY (regla_id) REFERENCES reglas(id) ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """)

    # Migración defensiva: copiar datos desde reglas.media_* si existen
    c.execute("SELECT id, media_url, media_tipo FROM reglas WHERE media_url IS NOT NULL")
    for rid, url, tipo in c.fetchall() or []:
        c.execute(
            """
            INSERT INTO regla_medias (regla_id, media_url, media_tipo)
            SELECT %s, %s, %s FROM DUAL
            WHERE NOT EXISTS (
                SELECT 1 FROM regla_medias WHERE regla_id=%s AND media_url=%s
            )
            """,
            (rid, url, tipo, rid, url),
        )

    # botones
    c.execute("""
    CREATE TABLE IF NOT EXISTS botones (
      id INT AUTO_INCREMENT PRIMARY KEY,
      mensaje   TEXT NOT NULL,
      tipo      VARCHAR(50),
      media_url TEXT,
      nombre    VARCHAR(100)
    ) ENGINE=InnoDB;
    """)
    # Migración defensiva para columnas nuevas
    c.execute("SHOW COLUMNS FROM botones LIKE 'tipo';")
    if not c.fetchone():
        c.execute("ALTER TABLE botones ADD COLUMN tipo VARCHAR(50) NULL;")
    c.execute("SHOW COLUMNS FROM botones LIKE 'media_url';")
    if not c.fetchone():
        c.execute("ALTER TABLE botones ADD COLUMN media_url TEXT NULL;")
    c.execute("SHOW COLUMNS FROM botones LIKE 'nombre';")
    if not c.fetchone():
        c.execute("ALTER TABLE botones ADD COLUMN nombre VARCHAR(100) NULL;")

    # boton_medias: soporta múltiples archivos por botón
    c.execute("""
    CREATE TABLE IF NOT EXISTS boton_medias (
      id INT AUTO_INCREMENT PRIMARY KEY,
      boton_id INT NOT NULL,
      media_url TEXT NOT NULL,
      media_tipo VARCHAR(20),
      FOREIGN KEY (boton_id) REFERENCES botones(id) ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """)

    # Migración defensiva: copiar datos desde botones.media_url si existen
    c.execute("SELECT id, media_url FROM botones WHERE media_url IS NOT NULL")
    for bid, url in c.fetchall() or []:
        c.execute(
            """
            INSERT INTO boton_medias (boton_id, media_url, media_tipo)
            SELECT %s, %s, NULL FROM DUAL
            WHERE NOT EXISTS (
                SELECT 1 FROM boton_medias WHERE boton_id=%s AND media_url=%s
            )
            """,
            (bid, url, bid, url),
        )

    # alias
    c.execute("""
    CREATE TABLE IF NOT EXISTS alias (
      numero VARCHAR(20) PRIMARY KEY,
      nombre VARCHAR(100)
    ) ENGINE=InnoDB;
    """)

    # chat_roles: relaciona cada número de chat con uno o varios roles
    c.execute("""
    CREATE TABLE IF NOT EXISTS chat_roles (
      numero  VARCHAR(20) NOT NULL,
      role_id INT NOT NULL,
      PRIMARY KEY (numero, role_id),
      FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """)

    # chat_state: almacena el paso actual y última actividad por número
    c.execute("""
    CREATE TABLE IF NOT EXISTS chat_state (
      numero VARCHAR(20) PRIMARY KEY,
      step TEXT,
      estado VARCHAR(20),
      last_activity DATETIME
    ) ENGINE=InnoDB;
    """)

    # Migración defensiva de la columna estado
    c.execute("SHOW COLUMNS FROM chat_state LIKE 'estado';")
    if not c.fetchone():
        c.execute("ALTER TABLE chat_state ADD COLUMN estado VARCHAR(20);")

    # ---- SEED admin (con PBKDF2 de Werkzeug) ----
    admin_hash = generate_password_hash('admin123')
    c.execute("""
    INSERT INTO usuarios (username, password)
      SELECT %s, %s FROM DUAL
      WHERE NOT EXISTS (SELECT 1 FROM usuarios WHERE username=%s)
    """, ('admin', admin_hash, 'admin'))

    c.execute("""
    INSERT INTO roles (name, keyword)
      SELECT %s, %s FROM DUAL
      WHERE NOT EXISTS (SELECT 1 FROM roles WHERE keyword=%s)
    """, ('Administrador', 'admin', 'admin'))

    c.execute("""
    INSERT INTO roles (name, keyword)
      SELECT %s, %s FROM DUAL
      WHERE NOT EXISTS (SELECT 1 FROM roles WHERE keyword=%s)
    """, ('Tiquetes', 'tiquetes', 'tiquetes'))

    c.execute("""
    INSERT INTO roles (name, keyword)
      SELECT %s, %s FROM DUAL
      WHERE NOT EXISTS (SELECT 1 FROM roles WHERE keyword=%s)
    """, ('Cotizar', 'cotizar', 'cotizar'))

    c.execute("""
    INSERT IGNORE INTO user_roles (user_id, role_id)
    SELECT u.id, r.id
      FROM usuarios u, roles r
     WHERE u.username=%s AND r.keyword=%s
    """, ('admin', 'admin'))

    conn.commit()
    conn.close()



def guardar_mensaje(
    numero,
    mensaje,
    tipo,
    wa_id=None,
    reply_to_wa_id=None,
    media_id=None,
    media_url=None,
    mime_type=None,
    link_url=None,
    link_title=None,
    link_body=None,
    link_thumb=None,
    step=None,
    regla_id=None,
):
    """Guarda un mensaje en la tabla ``mensajes``.

    Admite campos opcionales para los identificadores de WhatsApp
    (``wa_id`` y ``reply_to_wa_id``), para medios (``media_id``, ``media_url``,
    ``mime_type``) y, sólo para mensajes de tipo ``referral``, datos de enlaces
    (``link_url``, ``link_title``, ``link_body``, ``link_thumb``). También puede
    registrar el ``step`` del flujo y el ``regla_id`` que originó el mensaje.
    """
    if tipo != 'referral':
        link_url = link_title = link_body = link_thumb = None

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO mensajes "
        "(numero, mensaje, tipo, wa_id, reply_to_wa_id, media_id, media_url, mime_type, "
        "link_url, link_title, link_body, link_thumb, step, regla_id, timestamp) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
        (
            numero,
            mensaje,
            tipo,
            wa_id,
            reply_to_wa_id,
            media_id,
            media_url,
            mime_type,
            link_url,
            link_title,
            link_body,
            link_thumb,
            step,
            regla_id,
        ),
    )
    mensaje_id = c.lastrowid
    conn.commit()
    conn.close()
    return mensaje_id


def update_mensaje_texto(id_mensaje, texto):
    """Actualiza el campo `mensaje` de un registro existente."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "UPDATE mensajes SET mensaje=%s WHERE id=%s",
        (texto, id_mensaje),
    )
    conn.commit()
    conn.close()


def get_chat_state(numero):
    """Obtiene el step y last_activity almacenados para un número."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT step, last_activity FROM chat_state WHERE numero=%s",
        (numero,),
    )
    row = c.fetchone()
    conn.close()
    return row


def update_chat_state(numero, step, estado=None):
    """Inserta o actualiza el estado del chat y la última actividad."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "INSERT INTO chat_state (numero, step, estado, last_activity) VALUES (%s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE step=VALUES(step), estado=COALESCE(VALUES(estado), estado), last_activity=VALUES(last_activity)",
        (numero, step, estado),
    )
    conn.commit()
    conn.close()


def delete_chat_state(numero):
    """Elimina el registro de estado para un número."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("DELETE FROM chat_state WHERE numero=%s", (numero,))
    conn.commit()
    conn.close()

def obtener_mensajes_por_numero(numero):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
      SELECT mensaje, tipo, timestamp
      FROM mensajes
      WHERE numero = %s
      ORDER BY timestamp ASC
    """, (numero,))
    rows = c.fetchall()
    conn.close()
    return rows  # lista de tuplas (mensaje, tipo, timestamp)


def get_conversation(numero):
    """Obtiene la conversación de un número uniendo ``mensajes`` con ``reglas``.

    Realiza un ``JOIN`` entre ``mensajes`` y ``reglas`` usando ``regla_id`` y
    ordenando por ``reglas.id``. El resultado se devuelve en una sola fila con
    columnas dinámicas del tipo ``regla_step``, ``mensaje_usuario``,
    ``regla_step2``, ``mensaje_usuario_step2``, etc.
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT m.numero, r.step, m.mensaje
          FROM mensajes m
          JOIN reglas r ON m.regla_id = r.id
         WHERE m.numero = %s
         ORDER BY r.id
        """,
        (numero,),
    )
    rows = c.fetchall()
    conn.close()

    result = {"numero": numero}
    for idx, (_numero, step, mensaje) in enumerate(rows, start=1):
        if idx == 1:
            result["regla_step"] = step
            result["mensaje_usuario"] = mensaje
        else:
            result[f"regla_step{idx}"] = step
            result[f"mensaje_usuario_step{idx}"] = mensaje
    return result


def obtener_lista_chats():
    conn = get_connection()
    c    = conn.cursor(dictionary=True)
    # obtenemos cada número único, su último timestamp y alias si existe
    c.execute("""
      SELECT m.numero,
             (SELECT nombre FROM alias a WHERE a.numero=m.numero) AS alias,
             EXISTS(
               SELECT 1 FROM reglas r WHERE r.step='asesor' AND r.input_text=m.numero
             ) AS asesor
      FROM mensajes m
      GROUP BY m.numero
      ORDER BY MAX(m.timestamp) DESC;
    """)
    rows = c.fetchall()
    conn.close()
    return rows  # lista de dicts {numero, alias, asesor}


def obtener_botones():
    conn = get_connection()
    c    = conn.cursor(dictionary=True)
    c.execute("SELECT mensaje FROM botones ORDER BY id ASC;")
    rows = c.fetchall()
    conn.close()
    return [r['mensaje'] for r in rows]


def set_alias(numero, nombre):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
      INSERT INTO alias (numero, nombre)
      VALUES (%s, %s)
      ON DUPLICATE KEY UPDATE nombre = VALUES(nombre);
    """, (numero, nombre))
    conn.commit()
    conn.close()


def get_roles_by_user(user_id):
    """Retorna una lista de keywords de roles asignados a un usuario."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
      SELECT r.keyword
        FROM roles r
        JOIN user_roles ur ON r.id = ur.role_id
       WHERE ur.user_id = %s
    """, (user_id,))
    roles = [row[0] for row in c.fetchall()]
    conn.close()
    return roles


def assign_role_to_user(user_id, role_keyword, role_name=None):
    """Asigna un rol (por keyword) a un usuario. Si el rol no existe se crea."""
    conn = get_connection()
    c    = conn.cursor()
    # Obtener rol existente o crearlo
    c.execute("SELECT id FROM roles WHERE keyword=%s", (role_keyword,))
    row = c.fetchone()
    if row:
        role_id = row[0]
    else:
        name = role_name or role_keyword.capitalize()
        c.execute("INSERT INTO roles (name, keyword) VALUES (%s, %s)", (name, role_keyword))
        role_id = c.lastrowid
    # Asignar rol al usuario
    c.execute("INSERT IGNORE INTO user_roles (user_id, role_id) VALUES (%s, %s)", (user_id, role_id))
    conn.commit()
    conn.close()
