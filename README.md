# Dashboard Coppel

Dashboard ejecutivo de Contact Center alimentado exclusivamente por todos los `.parquet`
directamente en la carpeta privada de Google Drive `1VotCf0HuDIGbmkM88KYAQSZQRI0du-zB`,
inicialmente `3meses_v2.parquet` y `3meses_v3.parquet`. Se suman automaticamente al abrir,
recargar o cambiar filtros. La lista se consulta con paginacion; archivos nuevos o modificados
se descargan a `.drive_cache/` y se verifican por tamano y MD5. Los retirados dejan de sumarse.
Las subcarpetas, CSV y archivos de la raiz local se ignoran. Fallos de permisos, red, integridad
o lista incompleta muestran un error sin usar otras fuentes ni totales parciales.

Ver [configuracion privada local](../README.md#Acceso-privado-local-a-Drive).
En Cloud la credencial se carga de `[gcp_service_account]` en Secrets de Streamlit.
Ver [configuracion Cloud](../README.md#streamlit-community-cloud).
Localmente se carga desde `COPPEL_DRIVE_CREDENTIALS` o, por defecto,
`~/.config/coppel/drive-service-account.json`. Nunca guardarla en el proyecto.
Los maestros `Mae_contacto.xlsx`, `Mae_did.xlsx` y `Mae_lada.xlsx` se descargan de
la misma carpeta Drive, una unica version de cada archivo, en formato Excel XLSX.
Si falta uno, hay duplicados o el archivo es una hoja nativa de Google se muestra
un error; nunca se recurre a los maestros locales. La primera carga descarga y resume los datos;
las siguientes reutilizan copias y resumenes intactos. Los filtros y las cuatro pestanas
usan la misma lista de fuentes sincronizada.

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

Las partes CSV no alimentan ninguna pestana. Usar `split_by_dates.py --root <raiz> --downloads <Descargas> --repository-only` para generar partes sin mover ni modificar los originales. Para agregar llamadas al dashboard, subir un nuevo Parquet a la carpeta de Drive o actualizar uno existente, sin duplicar registros entre fuentes.

Con `--repository-only --output-dir <destino>` se pueden guardar los CSV fechados en otra carpeta, conservando los originales. El destino no debe contener CSV ni manifest.json previos. Los archivos temporales existentes no se sobrescriben.

Para incluir solo un intervalo de `Call end`, agregar `--start 2026-06-01 --end 2026-10-06` (ambos dias completos incluidos; fechas invalidas se excluyen). Requiere `--repository-only`. `--replace` reemplaza exclusivamente las partes identificadas por el manifiesto previo del destino personalizado, despues de verificar las nuevas; conserva los originales y otros archivos. El manifiesto registra el intervalo y los registros excluidos por fuente.

- Al abrir o recargar el dashboard, se sincronizan automaticamente todos los Parquet de la carpeta Drive, sin botones de actualizacion. Las cuatro pestanas, filtros, indicadores, graficos y exportaciones incluyen su suma.
- Los archivos son acumulativos: no se eliminan duplicados entre Parquet. No dejes versiones que contengan las mismas llamadas si no quieres contarlas dos veces.
- Actualiza `Mae_contacto.xlsx` en la carpeta Drive para cambiar la clasificacion (reemplazar la version, no dejar duplicados). Debe tener las columnas `Call Outcome name` y `Contacto / No Contacto`, con valores `Contacto` o `No Contacto`. El cruce ignora espacios al inicio/final y diferencias entre mayusculas y minusculas.
- La cache detecta archivos agregados, eliminados o modificados, y cambios en el maestro de contactos, por nombre, fecha de modificacion y tamano.
- El panel **Fuentes incluidas** muestra los Parquet y maestros de Drive usados. Si una fuente es invalida o el maestro falta, se muestra un error en lugar de calcular contactos con otra regla.

## Carga rapida e historico fijo

Las sesiones concurrentes comparten los metadatos y serializan la preparacion de cada resumen dentro del servidor: una sola lectura construye la cache de una fuente y las demas esperan para reutilizarla. No reiniciar el servidor durante la primera preparacion, pues los resumenes solo se publican una vez completos.

El detalle DID / Estado prepara su propio resumen la primera vez que se abre. Muestra el archivo y lote en proceso, la reutilizacion de resumenes y la agrupacion de resultados. Esta primera preparacion puede tardar varios minutos; las siguientes aperturas reutilizan la cache. Otras tareas de conversion o division comparten disco y CPU y pueden alargar esta preparacion.

### Presentacion del detalle

En **DETALLE DID / ESTADO**, un selector muestra solo una de las cinco tablas o graficos a la vez.
Las tablas tienen una altura de 700 pixeles y dibujan hasta 100 filas visibles por pagina, conservando el orden y
los controles de la jerarquia (padres e hijos pueden quedar en paginas distintas).
La busqueda considera todas las filas; la descarga incluye la jerarquia completa
que corresponde a la busqueda, incluso hijos contraidos y otras paginas.
Los totales siguen calculandose sobre todas las filas base, sin cambiar filtros,
fuentes, clasificacion, agregaciones ni cache.

La paginacion no se activa en otras pestanas.
Esta presentacion reduce elementos del navegador, pero no reduce los datos enviados
por cada tabla ni la preparacion inicial del resumen, y no garantiza solucionar
el error `removeChild` en Cloud; debe verificarse en el despliegue.

- La primera carga prepara un resumen SQLite por Parquet descargado en su carpeta `.dashboard_cache`, dentro de `.drive_cache`. No modifica ni elimina las fuentes remotas ni los originales locales.
- `3meses_v2.parquet` se mantiene incluido como historico. Mientras no cambie, las siguientes aperturas utilizan su resumen, incluso despues de reiniciar el dashboard o el equipo.
- Cuando crece o cambia un Parquet, solo se reconstruye su resumen completo; al agregar uno se prepara solo el nuevo. Si no cambio ninguna fuente, se usan sus resumenes directamente.
- Los resumenes conservan fecha, hora, tipo de llamada, campana, motivo de terminacion y resultado de llamada, con cantidades y sumas de tiempos. Se mantienen los filtros, graficos y la exportacion PPT.
- El maestro de contactos se aplica sobre los resumenes: cambiar Excel recalcula los contactos de todo el historico sin releer los Parquet.
- La lista de fuentes remotas, sus versiones y firmas detectan Parquet agregados, modificados y retirados de Drive. Los CSV y Parquet de la raiz se ignoran.
- La cache debe estar en una carpeta con permiso de escritura. Si se elimina `.dashboard_cache`, se regenera al abrir; una cache danada muestra un error y debe eliminarse para reconstruirla.

Desde el panel lateral puedes seleccionar **Generar PPT ejecutiva** para crear una presentación `.pptx` con los filtros activos. La presentación incluye portada, KPI, evolución mensual y horaria, mapa de calor, campañas principales y una diapositiva final con los insights semaforizados. Los gráficos y los iconos de apoyo se generan con fondo transparente u oscuro, según corresponda, para mantener el estilo corporativo.

## Definiciones

La vista principal tiene un banner ejecutivo azul con el logo local de Coppel (`assets/coppel-logo.png`) y el periodo activo, acentos amarillos, fondo claro y tarjetas con profundidad. Las tablas conservan sus encabezados azules, resumen mensual y de campanas, graficos en dos columnas y mapas de calor de contactabilidad por hora/dia. El encabezado se adapta a pantallas pequenas y no necesita descargar el logo de internet. Los KPI se muestran en cinco tarjetas: Total llamadas, Contactos, % de contactos, Idle time promedio y TMO. La cantidad y el porcentaje de contactos se presentan por separado, sin cambiar valores, unidades ni formulas. El analisis por campanas, los insights y la exportacion PPT siguen disponibles.

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
- `Mae_did.xlsx`, en la carpeta Drive, debe contener `DDI`, `NOMBRE` y `HOMOLOGACION`. Se cruza el DDI del Parquet con el maestro, ignorando ceros iniciales (por ejemplo, `0056` y `56`); la homologacion identifica el DID mostrado y NOMBRE identifica el proveedor.
- `Mae_lada.xlsx` debe contener `NIR` y `ESTADO`; se ignoran espacios en los encabezados. Se busca la lada de tres digitos del campo `Phone`, y luego la de dos si no existe la primera. Se admiten telefonos nacionales de diez digitos y prefijos internacionales `52` o `521`, con espacios, parentesis y guiones. No se adivina el estado de telefonos incompletos.
- Los registros sin coincidencia permanecen en **Sin cruce**, sin un aviso separado encima de las tablas. Maestros faltantes, bloqueados, incompletos o con claves contradictorias muestran un error en esta pestana sin ocultar el dashboard principal.
- La primera carga prepara resumenes adicionales por Parquet, conservando DDI y solo los primeros tres digitos validos del telefono, no el numero completo. No reemplaza los resumenes principales ni modifica las fuentes. Despues utiliza la cache persistente y solo prepara fuentes nuevas o modificadas. Los cambios en cualquiera de los maestros reclasifican estos resumenes sin releer los Parquet.

## Pestana RESULTADO LLAMADAS

La tabla ajusta sus dos columnas al ancho disponible: resultado/mes/fecha ocupa el 65% y llamadas el 35%. Los nombres largos se distribuyen en varias lineas para no ocultar los valores de llamadas.

## Pestana ANALISIS CONTACTABILIDAD

Diagnostico descriptivo que comparte filtros y usa los resumenes principales existentes, solo al seleccionar la pestana. Explica el KPI como contactos / llamadas (promedio ponderado), muestra el volumen de las campanas por encima del 95%, compara tipos de llamada, prioriza las campanas por cantidad de no contactos y desglosa los resultados y la cobertura del maestro. Incluye tablas con totales y el estilo del dashboard, composicion porcentual por campana y evolucion mensual.

Agrupa No Answer / Voicemail / Busy como falta de respuesta y Network Failure / Protocol Failure / Invalid Number / Incomplete Logging / Drop / Cancel como resultados tecnicos o de numeracion que requieren investigar. No modifica la clasificacion del Excel: un resultado clasificado Contacto conserva esa condicion; resultados desconocidos se identifican por separado. El grafico muestra las 15 campanas con mas no contactos; las tablas incluyen todas.

Separa hallazgos medibles de hipotesis sobre base, horarios, marcador, DID o mezcla operativa. No infiere causas demostradas ni personas unicas/intentos repetidos a partir de resumenes que no conservan el telefono. Los totales corresponden a los filtros globales, no a una busqueda local en la tabla.

Muestra una tabla jerarquica por **resultado de llamada / mes / fecha** y un grafico horizontal del porcentaje de cada resultado sobre el total de llamadas filtradas, ordenado por volumen descendente. Incluye todos los resultados, sin limitarse a los mas frecuentes. Las fechas corresponden a `Call end`.

Conserva encabezados azules y texto blanco, barras verdes de volumen en la tabla, primer nivel expandido y en negrilla, total fijo azul/blanco, ordenamiento, busqueda y barras de desplazamiento. El grafico usa azul claro y etiquetas de dos decimales. Comparte filtros y KPI; solo carga al seleccionar la pestana y utiliza los resumenes principales existentes, sin crear otra cache ni releer Parquet que no cambiaron.

- **Total llamadas:** cantidad de registros de la base después de aplicar filtros.
- **Contactos:** registros cuyo `Call Outcome name` figura como `Contacto` en `Mae_contacto.xlsx`. Los resultados no incluidos en el maestro no cuentan como contacto; no se usa `Talk Time` como alternativa.
- **% de contactos:** contactos / total llamadas; se muestra como 0% si no hay llamadas.
- **Iconos KPI:** auriculares para llamadas, persona con confirmacion para contactos, simbolo porcentual para contactabilidad, pausa para Idle y cronometro para TMO. Son SVG locales decorativos; no cambian los indicadores.
- **Idle time:** promedio de `Wait Time`.
- **TMO:** (`Talk Time` + `Wrap up time`) / total de registros.
- **Mapa de calor:** contactos agregados por día de semana y hora de `Call end`.

Se mantienen excluidas las campanas cuyo nombre contenga `test` o `prueba` y las llamadas sin fecha valida.