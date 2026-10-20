# Dashboard Coppel

Dashboard ejecutivo de Contact Center conectado exclusivamente a los archivos `.parquet`
directamente en la carpeta privada de Google Drive `1VotCf0HuDIGbmkM88KYAQSZQRI0du-zB`.
Suma todos los Parquet de esa carpeta al abrir, recargar o cambiar filtros. La lista se consulta
con paginacion; archivos nuevos o modificados se descargan a `.drive_cache/` y se verifican
por tamano y MD5. Los Parquet locales, CSV y subcarpetas de Drive se ignoran. No hay selector
de fuente local ni modo de prueba.

Drive debe contener los Parquet cruzados con columnas `Hora`, `conteo_registros`,
`Contacto / No Contacto`, `ALTURA DE MORA`, `Posicion`, `Estado`, `HOMOLOGACION`,
`DDI sin homologar`, `NIR 3 digitos sin homologar` y `Campaign Name sin homologar`.
Los Parquet antiguos sin esos cruces deben retirarse antes de cargar los nuevos; si queda
alguno, el dashboard muestra un error en vez de mezclar formatos. Los archivos son acumulativos:
no suba simultaneamente versiones que contengan las mismas llamadas.

El campo `conteo_registros` se usa como cantidad de llamadas; `Talk Time`, `Wait Time` y
`Wrap up time` contienen las sumas por grupo. No se usa `Total Calls` como contador.
`Contacto / No Contacto`, altura de mora y estado se leen del Parquet.

En el panel lateral, el rango se elige directamente con una barra de fechas. Los selectores de
**Mes** y **Año** están disponibles de forma independiente: se puede filtrar un mes sin elegir
primero el año, o escoger un rango/fecha puntual sin pasar por esos selectores. Si se combinan,
los criterios se aplican simultaneamente.

Ver [configuracion privada local](../README.md#Acceso-privado-local-a-Drive).
En Cloud la credencial se carga de `[gcp_service_account]` en Secrets de Streamlit.
Ver [configuracion Cloud](../README.md#streamlit-community-cloud).
Localmente se carga desde `COPPEL_DRIVE_CREDENTIALS` o, por defecto,
`~/.config/coppel/drive-service-account.json`. Nunca guardarla en el proyecto.
El unico maestro requerido es `Mae_did.xlsx`, XLSX, con una sola version en Drive.
Se usa para el nombre del proveedor; `HOMOLOGACION` ya viene en el Parquet. No se
requieren `Mae_contacto.xlsx`, `Mae_lada.xlsx` ni `Mae_Mora.xlsx`. La primera carga descarga
y resume los datos; las siguientes reutilizan copias y resumenes intactos.

**Altura de mora:** se toma de `ALTURA DE MORA` en el Parquet y se ordena por `Posicion`.
La campaña original se conserva para excluir nombres de test/prueba.

## Ejecucion

Desde esta carpeta:

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

El dashboard procesa cada Parquet en lotes de 250,000 filas, leyendo solo las columnas operativas necesarias para cada vista. Admite las columnas de texto de las copias convertidas y las columnas numericas nativas; un esquema incompleto o un archivo danado muestra un error con el nombre de la fuente.

## Actualizacion de fuentes

Para redistribuir las fuentes por fechas, `split_by_dates.py --root <raiz> --downloads <Descargas>` agrupa por la fecha real de `Call end` y crea archivos `llamadas_<inicio>_a_<fin>_parte_<numero>.csv` de maximo 24.000.000 bytes. Puede dividir un dia entre varias partes. Fechas invalidas se conservan aparte como `sin_fecha`. Verifica el rango de cada archivo, numero de registros y una huella SHA-256 acumulada independiente del orden antes de mover las fuentes previas al respaldo en Descargas. No elimina duplicados preexistentes. Ejecutar con el dashboard detenido.

Los CSV pueden dividirse con `split_sources.py --root <raiz> --downloads <Descargas>`: cada parte incluye el encabezado y ocupa como maximo 24.000.000 bytes, sin cortar registros (incluidos campos con saltos de linea). La herramienta verifica la reconstruccion byte por byte mediante SHA-256 y luego mueve los originales a una carpeta `Coppel_backup_<fecha>` en Descargas, con un manifiesto de tamanos y registros. Ejecutar con el dashboard detenido para evitar sumar originales y partes durante la sustitucion. Rechaza dividir de nuevo una raiz que ya contiene partes.

Las partes CSV no alimentan ninguna pestana. Usar `split_by_dates.py --root <raiz> --downloads <Descargas> --repository-only` para generar partes sin mover ni modificar los originales. Para agregar llamadas al dashboard, subir un nuevo Parquet cruzado a la carpeta de Drive o actualizar uno existente, sin duplicar registros entre fuentes ni conservar la version sin cruces.

Con `--repository-only --output-dir <destino>` se pueden guardar los CSV fechados en otra carpeta, conservando los originales. El destino no debe contener CSV ni manifest.json previos. Los archivos temporales existentes no se sobrescriben.

Para incluir solo un intervalo de `Call end`, agregar `--start 2026-06-01 --end 2026-10-06` (ambos dias completos incluidos; fechas invalidas se excluyen). Requiere `--repository-only`. `--replace` reemplaza exclusivamente las partes identificadas por el manifiesto previo del destino personalizado, despues de verificar las nuevas; conserva los originales y otros archivos. El manifiesto registra el intervalo y los registros excluidos por fuente.

- Al abrir o recargar el dashboard, se sincronizan automaticamente todos los Parquet de la carpeta Drive, sin botones de actualizacion. Las cuatro pestanas, filtros, indicadores, graficos y exportaciones incluyen su suma.
- Los archivos son acumulativos: no se eliminan duplicados entre Parquet. No dejes versiones que contengan las mismas llamadas si no quieres contarlas dos veces.
- La clasificacion de contacto, altura de mora, posicion y estado se lee directamente de cada Parquet cruzado.
- La cache detecta archivos Parquet agregados, eliminados o modificados y cambios en `Mae_did.xlsx`, por nombre, fecha de modificacion y tamano.
- Se suman todos los Parquet de Drive. Si un archivo no tiene los cruces requeridos o falta el maestro de proveedor, se muestra un error.

## Carga rapida e historico fijo

Las sesiones concurrentes comparten los metadatos y serializan la preparacion de cada resumen dentro del servidor: una sola lectura construye la cache de una fuente y las demas esperan para reutilizarla. No reiniciar el servidor durante la primera preparacion, pues los resumenes solo se publican una vez completos.

El detalle DID / Estado prepara su propio resumen diario la primera vez que se abre.
Conserva dia, tipo, campana, motivo, resultado, DDI y prefijo; no necesita la hora
para sus vistas mensuales ni para los filtros de dias completos. Lee los Parquet
en lotes de hasta 50.000 registros y los resumenes SQLite en lotes de hasta 25.000,
acumulando las cinco agrupaciones sin cargar un resumen completo en memoria.
La cache del detalle es independiente de los resumenes horarios de las otras pestanas.
Al actualizar desde la version anterior, se prepara una nueva cache del detalle una sola vez.
Muestra el archivo y lote en proceso y reutiliza los resumenes intactos; la primera
preparacion puede tardar varios minutos. Se conservan hasta dos resultados filtrados
en la cache de memoria del detalle. Los cambios de maestros reclasifican sin releer
Parquet intactos. Otras tareas de conversion o division comparten disco y CPU.

La pestana **RESULTADO LLAMADAS** presenta la jerarquia **Resultado / Mes / Estado / Fecha**.
Su resumen diario por estado se prepara la primera vez que se abre la pestana y se guarda
en la cache SQLite; esa preparacion inicial puede tardar. El estado se toma del Parquet cruzado.

### Presentacion del detalle

En **DETALLE DID / ESTADO**, un selector muestra solo una de las cinco tablas o graficos a la vez.
Las tablas tienen una altura de 700 pixeles, encabezados azules con fuente blanca
y subtotales en negrilla, con 48 pixeles adicionales de sangria por nivel.
Se muestran mediante HTML estatico sanitizado por Streamlit, sin JavaScript ni iframe.
El servidor envia solo hasta 100 filas por pagina mas una fila de total general.
Los controles ocupan dos filas: componente y una fila con busqueda, niveles, orden y pagina.
El selector de niveles reemplaza la expansion individual
de ramas; cada fila muestra su ruta completa para identificar padres e hijos entre paginas.
La busqueda considera toda la jerarquia y conserva los antecesores de las coincidencias.
Los controles **Ordenar por** y **Orden** ordenan hermanos antes de paginar.
**Preparar CSV completo** genera bajo demanda la descarga de todos los niveles y
paginas que corresponden a la busqueda, independientemente del nivel visible.
La busqueda, los niveles y la paginacion se ejecutan en un fragmento de Streamlit,
sin volver a ejecutar las otras pestanas ni sincronizar Drive por cada pagina.
Los totales siguen calculandose sobre todas las filas base, sin cambiar filtros,
fuentes, clasificacion ni reglas de agregacion.

La paginacion no se activa en otras pestanas.
Estas optimizaciones reducen la memoria y los datos enviados por tabla, pero no garantizan solucionar
el error `removeChild` en Cloud; debe verificarse en el despliegue.

- La primera carga prepara un resumen SQLite por Parquet descargado en su carpeta `.dashboard_cache`, dentro de `.drive_cache`. No modifica ni elimina las fuentes remotas ni los originales locales.
- Cuando crece o cambia un Parquet, solo se reconstruye su resumen completo; al agregar uno se prepara solo el nuevo. Si no cambio ninguna fuente, se usan sus resumenes directamente.
- Los resumenes conservan las dimensiones y metricas de los Parquet cruzados, incluyendo contactos, altura de mora, estado, cantidades y sumas de tiempos.
- `Mae_did.xlsx` completa el nombre del proveedor; no reclasifica contactos ni estado.
- La lista de fuentes remotas, sus versiones y firmas detectan Parquet agregados, modificados y retirados de Drive. Los CSV y Parquet de la raiz se ignoran.
- La cache debe estar en una carpeta con permiso de escritura. Si se elimina `.dashboard_cache`, se regenera al abrir; una cache danada muestra un error y debe eliminarse para reconstruirla.

La barra de fechas permite seleccionar el rango de manera directa. Los filtros **Mes** y
**Año** funcionan independientemente y se pueden combinar con ese rango.

La exportacion PDF de recurrencia y su generador independiente fueron retirados. El dashboard ya no genera ni descarga ese reporte. Tampoco muestra exportaciones de resultados sin maestro: esos cruces ya vienen incluidos en los Parquet.

## Definiciones

La vista principal tiene un banner ejecutivo azul con el logo local de Coppel (`assets/coppel-logo.png`) y el periodo activo, acentos amarillos, fondo claro y tarjetas con profundidad. Las tablas conservan sus encabezados azules, resumen mensual y de campanas, graficos en dos columnas y mapas de calor de contactabilidad por hora/dia. El encabezado se adapta a pantallas pequenas y no necesita descargar el logo de internet. Los KPI se muestran en cinco tarjetas: Total llamadas, Contactos, % de contactos, Idle time promedio y TMO. La cantidad y el porcentaje de contactos se presentan por separado, sin cambiar valores, unidades ni formulas. El analisis por campanas sigue disponible; los insights semaforizados ahora estan en la pestana ANALISIS CONTACTABILIDAD.

Los mapas visuales muestran porcentajes de contactos sobre llamadas de cada celda (no cantidades); una celda sin llamadas se muestra como `—`. El detalle por dia del mes y hora se obtiene de los resumenes persistentes, sin releer los CSV. Esto no modifica el calculo de los KPI ni la base usada por el PPT.

Las tablas y graficos debajo de los KPI no tienen bordes exteriores y usan mayor separacion vertical entre bloques. Las tarjetas KPI conservan sus bordes y estilo.

Las tablas mensual y de campanas son interactivas, con encabezados azules y texto blanco: pulsa un encabezado para ordenar por llamadas, contactos, contactabilidad o tiempos; pulsa otra vez para invertir el orden. Los valores se ordenan como numeros, no como texto. Tienen columnas amplias y barra inferior de desplazamiento horizontal para ver toda la informacion sin ampliar la tabla. Incluyen una fila Total fija al pie, azul con texto blanco, con llamadas y contactos del periodo filtrado, contactabilidad y tiempos ponderados por llamadas; no cambia al ordenar o buscar localmente. Cada tabla permite buscar, ampliar a pantalla completa y descargar en CSV las filas visibles con el orden aplicado. Se renderizan como un componente HTML local, sin librerias externas.

Los graficos muestran siempre la barra de herramientas de Plotly: zoom, acercar/alejar, desplazamiento, restablecer e imagen. El boton de pantalla completa amplia el grafico. Los mapas de calor conservan su formato de tabla.

Los graficos de contactabilidad por dia de semana y dia del mes tienen 344 px de alto cada uno, conservando el ancho de su columna. Junto con el espacio entre ellos, su conjunto se alinea con la altura del mapa de calor semanal; el eje vertical deja espacio para las etiquetas de los valores maximos.

El grafico de campanas ocupa el ancho completo y compara dos paneles alineados por campana: participacion en llamadas (azul claro) y contactabilidad (azul oscuro), con escalas independientes. No suma porcentajes de bases distintas. Muestra las 15 campanas de mayor volumen, ordenadas por llamadas, con etiquetas horizontales de dos decimales fuera de las barras; al pasar el mouse se ven el nombre completo, llamadas y contactos. Las tablas estan en una fila y los graficos mensuales en otra para dar espacio a esta comparacion.

## Pestana DETALLE DID / ESTADO

- Comparte los filtros, periodo y cinco KPI del dashboard principal. Las tablas tienen los mismos encabezados azules/blancos, ordenamiento numerico, busqueda, descarga, pantalla completa y barras de desplazamiento horizontal/vertical.
- Solo se calcula el contenido de la pestana seleccionada: abrir el dashboard principal no carga los resumenes de DID/estado, para conservar su rapidez.
- Incluye jerarquias expandibles **DID / Mes / Proveedor**, **DID / Campana / Mes** y **Estado / Mes / Proveedor**. El primer nivel se abre por defecto; pulsa `+` para ver niveles inferiores o `−` para contraer. El ordenamiento se aplica entre filas del mismo nivel; los subtotales no se suman otra vez en los KPI.
- El nivel principal de las tres tablas de detalle se muestra en negrilla. Cada tabla tiene una fila Total fija azul/blanca: suma las llamadas y contactos una sola vez y calcula contactabilidad, TMO e Idle ponderados por llamadas. El total corresponde a los filtros del dashboard, independientemente de la expansion, ordenamiento o busqueda local.
- Muestra llamadas y contactabilidad por DID, y un diagrama TMO/contactabilidad por estado con lineas de referencia del promedio ponderado de las llamadas filtradas.
- El diagrama por estado muestra los nombres y metricas al pasar el cursor sobre cada punto, sin etiquetas permanentes superpuestas.
- `Mae_did.xlsx`, en la carpeta Drive, debe contener `DDI`, `NOMBRE` y `HOMOLOGACION`. Se cruza el DDI del Parquet con el maestro, ignorando ceros iniciales (por ejemplo, `0056` y `56`), para completar el nombre del proveedor.
- DID, homologacion y estado ya vienen cruzados en el Parquet. No se consulta `Mae_lada.xlsx`; los registros cuya clasificacion ya venga como **Sin cruce** permanecen asi.
- Si el maestro de proveedor falta, esta bloqueado, incompleto o tiene claves contradictorias, esta pestana muestra un error sin ocultar el dashboard principal.
- La primera carga prepara resumenes adicionales por Parquet con las dimensiones ya cruzadas. No reemplaza los resumenes principales ni modifica las fuentes. Despues utiliza la cache persistente y solo prepara fuentes nuevas o modificadas.

## Pestana RESULTADO LLAMADAS

La tabla ajusta sus dos columnas al ancho disponible: resultado/mes/fecha ocupa el 65% y llamadas el 35%. Los nombres largos se distribuyen en varias lineas para no ocultar los valores de llamadas.

La tabla muestra los resultados agrupados por Resultado / Mes / Estado / Fecha. El filtro global **Estado** sigue aplicando a esta pestaña y al resto del dashboard.

El contenido de la pestana solo se prepara al abrirla. En esa primera carga se construyen resumenes persistentes por dia, resultado y estado. Se lee el Parquet en lotes y se muestra el avance. Las siguientes visitas reutilizan la cache SQLite en lugar de recorrer los resumenes horarios.

## Filtro Estado (panel lateral)

El filtro **Estado** aplica a todo el dashboard. Sin estados seleccionados se usan los resumenes originales, por lo que las cifras no cambian. La primera seleccion prepara una cache por estado; despues responde en segundos.

## Pestana ANALISIS CONTACTABILIDAD

Vista orientada a decisiones que comparte filtros y usa los resumenes principales existentes, solo al seleccionar la pestana. Se organiza en tres bloques:

1. **Decisiones prioritarias:** seis tarjetas semaforizadas (🚨 Urgente, ⚠️ Vigilar, ✅ Mantener) ordenadas por urgencia, cada una con el hallazgo, **Que hacer** y **Que no hacer**: campana que mas contactos pierde, llamadas sin respuesta, fallas tecnicas y numeros invalidos, mejor y peor hora, TMO mas alto y campana modelo. Los insights que antes estaban en el dashboard principal se trasladaron aqui.
2. **Donde se pierden los contactos:** grafico de campanas con mas no contactos y tablas desplegables por campana y por resultado.
3. **Tendencia mensual:** grafico y tabla desplegable de comparabilidad por campana.

Agrupa No Answer / Voicemail / Busy como falta de respuesta y Network Failure / Protocol Failure / Invalid Number / Incomplete Logging / Drop / Cancel como resultados tecnicos o de numeracion que requieren investigar. Respeta la clasificacion `Contacto / No Contacto` del Parquet; el grafico muestra las 15 campanas con mas no contactos y las tablas incluyen todas.

Separa hallazgos medibles de recomendaciones. No infiere causas demostradas ni personas unicas/intentos repetidos a partir de resumenes que no conservan el telefono. Los totales corresponden a los filtros globales, no a una busqueda local en la tabla.

Muestra una tabla jerarquica por **resultado de llamada / mes / fecha** y un grafico horizontal del porcentaje de cada resultado sobre el total de llamadas filtradas, ordenado por volumen descendente. Incluye todos los resultados, sin limitarse a los mas frecuentes. Las fechas corresponden a `Call end`.

Conserva encabezados azules y texto blanco, barras verdes de volumen en la tabla, primer nivel expandido y en negrilla, total fijo azul/blanco, ordenamiento, busqueda y barras de desplazamiento. El grafico usa azul claro y etiquetas de dos decimales. Comparte filtros y KPI; solo carga al seleccionar la pestana y utiliza los resumenes principales existentes, sin crear otra cache ni releer Parquet que no cambiaron.

- **Total llamadas:** cantidad de registros de la base después de aplicar filtros.
- **Contactos:** registros clasificados como `Contacto` en el campo `Contacto / No Contacto` del Parquet cruzado.
- **% de contactos:** contactos / total llamadas; se muestra como 0% si no hay llamadas.
- **Iconos KPI:** auriculares para llamadas, persona con confirmacion para contactos, simbolo porcentual para contactabilidad, pausa para Idle y cronometro para TMO. Son SVG locales decorativos; no cambian los indicadores.
- **Idle time (tarjeta KPI):** promedio de `Wait Time` solo de llamadas de marcador (`dialer`).
- **TMO (tarjeta KPI):** (`Talk Time` + `Wrap up time`) / llamadas de marcador. Los tipos `manual` y `preview` / vista previa no se miden en estas dos tarjetas: si el filtro de Call type solo deja esos tipos, muestran **N/A**. Las demas metricas, tablas y graficos no cambian.
- **Mapa de calor:** contactos agregados por día de semana y hora de `Call end`.

Se mantienen excluidas las campanas cuyo nombre contenga `test` o `prueba` y las llamadas sin fecha valida.