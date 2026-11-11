param([string]$Project, [string]$Scratch, [int]$Port = 3000)
# v34: there is no Python on the owner's computer, so changes are checked in the desktop app's built-in browser (nothing is
# installed): run this file in the background with -Project <repo folder> -Scratch <this folder>, then open
#   http://localhost:3000/check.html       -> real Python (Pyodide): compile + pyflakes on every .py, tests/test_core.py, the middleware
#   http://localhost:3000/check-pages.html -> v35: tests/test_pages.js, then real pages made by api/page.js, stored here and
#                                             opened at their real addresses (/stock/KO, /he/stock/KO, /learn/cup-and-handle ...)
#   http://localhost:3000/                 -> the local index.html with the new CSP, reading the live API through /proxy (GET only)
#   /__set?block=1 makes /proxy refuse the per-stock answers, as the API's robots.txt does for Google. /__set?country=IL is /api/geo's answer.
# Read-only: it serves only the site's own files and what check-pages.html stored in memory. Stops by itself after an hour.
Add-Type -AssemblyName System.Net.Http
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$http = New-Object System.Net.Http.HttpClient
$http.Timeout = [TimeSpan]::FromSeconds(60)
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add("http://localhost:$Port/")
$l.Start()
"listening on $Port"
$csp = ((Get-Content -LiteralPath (Join-Path $Project 'vercel.json') -Raw -Encoding UTF8 | ConvertFrom-Json).headers | ForEach-Object { $_.headers } | Where-Object { $_.key -eq 'Content-Security-Policy' }).value
$types = @{ '.html'='text/html; charset=utf-8'; '.js'='application/javascript'; '.json'='application/json'; '.png'='image/png'; '.svg'='image/svg+xml'; '.xml'='application/xml'; '.txt'='text/plain; charset=utf-8'; '.py'='text/plain; charset=utf-8'; '.csv'='text/plain; charset=utf-8' }
$stored = @{}
$block = $false
$down = ''
$country = 'IL'
$started = Get-Date
while ($l.IsListening -and ((Get-Date) - $started).TotalMinutes -lt 60) {
    $ctxTask = $l.GetContextAsync()
    while (-not $ctxTask.AsyncWaitHandle.WaitOne(1000)) {
        if (((Get-Date) - $started).TotalMinutes -ge 60) { break }
    }
    if (-not $ctxTask.IsCompleted) { break }
    $c = $ctxTask.Result
    try {
        $path = [Uri]::UnescapeDataString($c.Request.Url.AbsolutePath)
        $file = $null
        if ($path -eq '/check.html' -or $path -eq '/check-pages.html') { $file = Join-Path $Scratch $path.TrimStart('/') }
        elseif ($path -eq '/store' -and $c.Request.HttpMethod -eq 'POST') {
            $reader = New-Object IO.StreamReader($c.Request.InputStream, [Text.Encoding]::UTF8)
            $stored[$c.Request.QueryString['path']] = @{ body = $reader.ReadToEnd(); type = $c.Request.QueryString['type'] }
            $c.Response.Close()
            continue
        }
        elseif ($path -eq '/__set') {
            if ($c.Request.QueryString['block']) { $block = $c.Request.QueryString['block'] -eq '1' }
            if ($c.Request.QueryString['country']) { $country = $c.Request.QueryString['country'] }
            if ($c.Request.QueryString['down']) { $down = $c.Request.QueryString['down'] }
            $bytes = [Text.Encoding]::UTF8.GetBytes("block=$block country=$country down=$down")
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($path -eq '/api/geo') {
            $bytes = [Text.Encoding]::UTF8.GetBytes("{`"country`":`"$country`"}")
            $c.Response.ContentType = 'application/json'
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($path -eq '/list') {
            $names = @()
            Get-ChildItem -LiteralPath $Project -Filter *.py -File | ForEach-Object { $names += $_.Name }
            Get-ChildItem -LiteralPath (Join-Path $Project 'tests') -Filter *.py -File | ForEach-Object { $names += "tests/$($_.Name)" }
            Get-ChildItem -LiteralPath (Join-Path $Project 'tests\fixtures') -File | ForEach-Object { $names += "tests/fixtures/$($_.Name)" }
            $bytes = [Text.Encoding]::UTF8.GetBytes(($names -join "`n"))
            $c.Response.ContentType = 'text/plain; charset=utf-8'
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($path.StartsWith('/src/')) {
            $rel = $path.Substring(5).Replace('/', '\')
            if ($rel -notmatch '\.\.' -and ($rel -match '\.(py|csv|html|js|json|txt)$' -or $rel -eq '.vercelignore')) { $file = Join-Path $Project $rel }
        }
        elseif ($path.StartsWith('/proxy/')) {
            # the live API refuses other origins, so the local copy of the page reads it through here (GET only)
            # v35c: down=db = the database is suspended (only what comes from Yahoo/SEC still answers), down=all = the server is dead
            if ($down -eq 'all' -or ($down -eq 'db' -and $path -notmatch '^/proxy/api/(lookup|market-ticker|market-movers|technical-setup|stock-news|fear-greed|sector-performance|price-compare|ping)')) {
                $c.Response.StatusCode = 503
                $c.Response.Close()
                continue
            }
            if ($block -and $path -match '^/proxy/api/(lookup|technical-setup|fundamentals|stock-news|sector-comparison|signal-history|insider|street|ai-note|funds/stock|company-profile)/') {
                $c.Response.StatusCode = 403
                $c.Response.Close()
                continue
            }
            $target = 'https://swing-dashboard-3btx.onrender.com' + $c.Request.Url.PathAndQuery.Substring(6)
            $r = $http.GetAsync($target).Result
            $bytes = $r.Content.ReadAsByteArrayAsync().Result
            $c.Response.StatusCode = [int]$r.StatusCode
            $c.Response.ContentType = 'application/json'
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($stored.ContainsKey($path)) {
            $html = $stored[$path].body.Replace('https://swing-dashboard-3btx.onrender.com', "http://localhost:$Port/proxy")
            $bytes = [Text.Encoding]::UTF8.GetBytes($html)
            $c.Response.Headers.Add('Content-Security-Policy', $csp)
            $c.Response.ContentType = $stored[$path].type
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($path -eq '/' -or $path -eq '/index.html' -or $path.StartsWith('/stock/') -or $path -eq '/he' -or $path.StartsWith('/he/stock/') -or
                $path -match '^(/he)?/(stock|portfolio|market(/(stocks|calendar|events))?|performance(/stats)?)$') {   # v49: every screen's address
            $html = [IO.File]::ReadAllText((Join-Path $Project 'index.html'), [Text.Encoding]::UTF8).Replace('https://swing-dashboard-3btx.onrender.com', "http://localhost:$Port/proxy")
            $bytes = [Text.Encoding]::UTF8.GetBytes($html)
            $c.Response.Headers.Add('Content-Security-Policy', $csp)
            $c.Response.ContentType = 'text/html; charset=utf-8'
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $c.Response.Close()
            continue
        }
        elseif ($path -match '^/[A-Za-z0-9._-]+\.(png|svg|json|js|xml|txt)$' -and $path -ne '/vercel.json') { $file = Join-Path $Project $path.TrimStart('/') }
        if ($file -and (Test-Path -LiteralPath $file)) {
            $bytes = [IO.File]::ReadAllBytes($file)
            $ext = [IO.Path]::GetExtension($file).ToLower()
            $c.Response.ContentType = if ($types[$ext]) { $types[$ext] } else { 'application/octet-stream' }
            $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $c.Response.StatusCode = 404
        }
    } catch { try { $c.Response.StatusCode = 500 } catch {} }
    try { $c.Response.Close() } catch {}
}
$l.Stop()
