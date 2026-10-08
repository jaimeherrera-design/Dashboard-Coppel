# Reporte de gestion y telefonia Coppel

Todo lo necesario para ejecutar el proyecto local se encuentra en esta carpeta.

## Estructura

- `dashboard_coppel/`: codigo de las cuatro pestanas, pruebas, dependencias, documentacion y logo.
- `*.parquet` en la raiz: copias locales conservadas; no alimentan el dashboard.
- `3meses_v2.csv`, `3meses_v3.csv`: originales conservados; no alimentan el dashboard.
- `csv_repositorio/`: partes de maximo 24.000.000 bytes con rangos de fechas en el nombre. Son archivos para organizacion del repositorio y no se suman al dashboard.
- `Mae_contacto.xlsx`, `Mae_did.xlsx`, `Mae_lada.xlsx` en la raiz: copias locales; los maestros activos se leen de Drive.
- `Lanzar_Coppel.bat`: lanzador local para Windows.
- `.vscode/tasks.json`: tarea de ejecucion en VS Code.
- `.venv/`: entorno local utilizado por el lanzador.
- `.dashboard_cache/`: resumenes activos por fuente; necesarios para evitar reprocesar los CSV en cada apertura.
- `.drive_cache/`: copias descargadas de Drive y resumenes por archivo; datos privados excluidos de Git.

El dashboard suma automaticamente todos los Parquet directamente en la carpeta privada de
[Google Drive](https://drive.google.com/drive/folders/1VotCf0HuDIGbmkM88KYAQSZQRI0du-zB)
al abrir, recargar o cambiar filtros. Tambien descarga los tres maestros XLSX de esa carpeta.
Ignora los archivos de la raiz local, subcarpetas de Drive y otros archivos.
Agregar Parquet con registros nuevos, sin repetir llamadas ya presentes en otras fuentes: no se eliminan duplicados.
Los archivos retirados de Drive dejan de sumarse; sus copias de cache pueden permanecer en disco pero no se seleccionan.

## Acceso privado local a Drive

Habilitar Google Drive API en el proyecto Google Cloud de la cuenta de servicio y compartir
la carpeta con el correo de esa cuenta como **Lector**. Los nuevos archivos deben heredar
el permiso de la carpeta. No se requieren roles de propietario o editor del proyecto.

Guardar el JSON de la cuenta fuera del proyecto y de OneDrive:
`C:\Users\jaime.herrera\.config\coppel\drive-service-account.json`.
El programa usa `~/.config/coppel/drive-service-account.json` por defecto.
Para otra ubicacion, configurar `COPPEL_DRIVE_CREDENTIALS` antes de lanzar Streamlit.
Nunca subir el JSON a Git ni compartir su contenido. El alcance usado es `drive.readonly`;
el programa no sube, modifica ni elimina archivos de Drive.

Se consulta la lista completa con paginacion en cada ejecucion, descargando solo fuentes
nuevas o modificadas, verificando tamano y MD5 y publicando cada copia completa.
Una descarga fallida o una carpeta que cambia durante la sincronizacion muestra un error;
no se calculan cifras parciales ni se recurre a los Parquet locales.
Los maestros Excel se descargan de la misma carpeta Drive y deben llamarse exactamente
`Mae_contacto.xlsx`, `Mae_did.xlsx` y `Mae_lada.xlsx` (se ignoran mayusculas/minusculas).
Debe existir una sola version de cada maestro; duplicados, faltantes y hojas nativas
de Google con esos nombres muestran un error. Los cambios reclasifican los resumenes
sin volver a leer Parquet intactos. No hay sustitucion por maestros locales.

## Streamlit Community Cloud

Subir a GitHub el codigo completo de `dashboard_coppel/`, incluyendo
`requirements.txt`, modulos auxiliares y `assets/coppel-logo.png`. No subir datos,
credenciales ni caches. Seleccionar `dashboard_coppel/app.py` como archivo principal.
Cloud debe poder instalar las dependencias de `dashboard_coppel/requirements.txt`.

En la aplicacion de Streamlit, abrir **Settings > Secrets** y agregar la cuenta de
servicio bajo `[gcp_service_account]` en formato TOML. Copiar los valores del JSON
local de forma privada, directamente al editor de Secrets:

```toml
[gcp_service_account]
type = "service_account"
project_id = "SU_PROYECTO"
private_key_id = "SU_ID_DE_CLAVE"
private_key = """-----BEGIN PRIVATE KEY-----
CONTENIDO_DE_LA_CLAVE
-----END PRIVATE KEY-----
"""
client_email = "SU_CUENTA@SU_PROYECTO.iam.gserviceaccount.com"
client_id = "SU_CLIENT_ID"
token_uri = "https://oauth2.googleapis.com/token"
```

El bloque es solo una plantilla, no una credencial. La clave debe conservar sus
saltos de linea reales. Nunca guardar los valores reales en GitHub ni en el chat.
Secrets tiene prioridad sobre el archivo local; si existe una seccion invalida
se muestra un error en lugar de usar otra credencial. Guardar Secrets y reiniciar
la aplicacion. No es necesario subir Excel ni Parquet a GitHub.

La primera carga de Cloud descarga y resume los datos; su cache puede perderse
cuando el servicio reinicia o duerme. La memoria y el disco disponibles en el
plan deben ser suficientes: el despliegue Cloud y sus limites aun deben validarse
en la cuenta del usuario, aunque la conexion local y los maestros esten verificados.

## Instalacion desde una copia del codigo

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\dashboard_coppel\requirements.txt
.\.venv\Scripts\streamlit.exe run .\dashboard_coppel\app.py --server.port 8504
```

Antes de ejecutar, configurar la credencial y el permiso en Drive, y colocar los tres maestros
autorizados en la carpeta Drive. En una instalacion nueva se descargan las fuentes y se reconstruyen
los resumenes durante la primera carga. Solo se preparan los archivos nuevos o modificados;
los demas reutilizan su cache. Se necesita conexion a Drive incluso cuando hay cache local.

## GitHub y datos privados

`.gitignore` excluye CSV, Parquet, Excel, entorno virtual, cache, archivos temporales y exportaciones. Las fuentes contienen telefonos e identificadores de agentes: no publicarlas ni compartirlas sin autorizacion y tratamiento de privacidad adecuado. Los datos siguen disponibles localmente, pero no se incluyen al agregar el codigo a Git.

Se conservan las pruebas y el divisor de CSV porque permiten validar y mantener el proyecto. No son archivos sobrantes.

## Copias Parquet

`dashboard_coppel/convert_parquet.py --root <raiz> --exclude-date 2026-10-07`
genera `3meses_v2.parquet` y `3meses_v3.parquet` junto a los originales, sin modificarlos.
Excluye el dia completo indicado segun `Call end`; conserva otras fechas y valores
sin fecha valida. Todas las columnas se guardan como texto para preservar ceros
iniciales, identificadores, campos vacios y los valores originales. Usa compresion
Zstandard y verifica esquema, cantidades y huellas del contenido por grupo.
No sobrescribe Parquet existentes. Para incorporarlos al dashboard deben subirse a la carpeta
configurada de Drive; las copias en la raiz no se cargan.
Los Parquet tambien se excluyen de Git por contener datos operativos privados.

Ver [documentacion funcional](dashboard_coppel/README.md).
