package uk.aive.couple

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject

/**
 * couple.ai-ve.uk 를 전체화면 WebView 로 감싸는 단일 액티비티.
 *
 * 웹앱(PWA)을 그대로 띄우되, 일반 브라우저에서는 불가능한 것들을 네이티브로 보강한다:
 *  - 세션 쿠키 영속(90일 매직링크 세션이 콜드스타트에도 유지)
 *  - <input type=file> 사진 다중 업로드 / 다운로드(DownloadManager)
 *  - window.CoupleNative.notify(...) → 시스템 알림(콕찌르기·리마인더)
 *  - 뒤로가기 2번 종료, 외부 링크는 외부 앱으로 위임
 */
class MainActivity : ComponentActivity() {

    companion object {
        private const val CHANNEL_ID = "couple_alerts"
    }

    private lateinit var webView: WebView
    @Volatile private var pageHost: String? = null   // 현재 메인 프레임 호스트(JS 브리지 오리진 가드용)
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var lastBackMs = 0L
    private var exitToast: Toast? = null

    // <input type=file> → 안드로이드 파일 선택기. 결과를 ValueCallback 으로 WebView 에 반환.
    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val cb = filePathCallback
        filePathCallback = null
        if (cb == null) return@registerForActivityResult
        if (result.resultCode != Activity.RESULT_OK || result.data == null) {
            cb.onReceiveValue(null)
            return@registerForActivityResult
        }
        val data = result.data!!
        val uris = mutableListOf<Uri>()
        val clip = data.clipData
        if (clip != null) {
            for (i in 0 until clip.itemCount) uris.add(clip.getItemAt(i).uri)
        } else {
            data.data?.let { uris.add(it) }
        }
        cb.onReceiveValue(if (uris.isEmpty()) null else uris.toTypedArray())
    }

    // POST_NOTIFICATIONS(Android 13+). 거부해도 앱은 동작 — 알림만 안 뜸.
    private val notifPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* no-op */ }

    // 위치 권한 — 지도 '내 위치'(WebView geolocation)용. 거부해도 나머진 동작.
    private val locationPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* no-op */ }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createNotificationChannel()
        ensureNotifPermission()
        ensureLocationPermission()

        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
        webView = WebView(this).apply { configure() }
        webView.addJavascriptInterface(CoupleBridge(), "CoupleNative")
        setContentView(webView)

        // 뒤로가기: WebView 히스토리가 있으면 그걸 먼저 타고, 최상단에서 2초 내 두 번 → 종료.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                    return
                }
                val now = System.currentTimeMillis()
                if (now - lastBackMs < 2000L) {
                    exitToast?.cancel()
                    finish()
                } else {
                    lastBackMs = now
                    exitToast?.cancel()
                    exitToast = Toast.makeText(this@MainActivity, "한 번 더 누르면 종료돼요", Toast.LENGTH_SHORT).also { it.show() }
                }
            }
        })

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl(BuildConfig.COUPLE_URL)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun WebView.configure() {
        // 세션 쿠키 영속화 — 90일 매직링크 세션이 앱 재시작에도 살아남게.
        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        cm.setAcceptThirdPartyCookies(this, true)

        settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true            // Alpine.js / localStorage 필수
            databaseEnabled = true
            userAgentString = userAgentString.replace("; wv", "")
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            useWideViewPort = true
            loadWithOverviewMode = true
            builtInZoomControls = false
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = false
            defaultTextEncodingName = "UTF-8"
            cacheMode = WebSettings.LOAD_DEFAULT
        }

        webViewClient = object : WebViewClient() {
            // 메인 프레임이 가리키는 호스트를 추적 — JS 브리지는 이 값이 우리 도메인일 때만 동작.
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                pageHost = url?.let { Uri.parse(it).host }
            }
            // 앱(couple.ai-ve.uk) + 로그인 흐름(Cloudflare Access·Google OAuth)은 WebView 안에서 처리.
            // 그래야 Access 인증 쿠키가 WebView 의 CookieManager 에 적재된다.
            // 카카오맵 길찾기·tel:·mailto: 같은 진짜 외부 링크만 외부 앱으로 위임.
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val scheme = url.scheme ?: ""
                if (scheme == "blob" || scheme == "data") return false
                if ((scheme == "http" || scheme == "https") && isInAppHost(url.host)) {
                    return false
                }
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                    true
                } catch (_: Exception) {
                    false
                }
            }
        }

        webChromeClient = object : WebChromeClient() {
            // 지도 '내 위치' — WebView geolocation 요청을 허용한다(앱이 OS 위치권한 보유 시).
            override fun onGeolocationPermissionsShowPrompt(
                origin: String?,
                callback: android.webkit.GeolocationPermissions.Callback?
            ) {
                val granted = ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.ACCESS_FINE_LOCATION
                ) == PackageManager.PERMISSION_GRANTED ||
                    ContextCompat.checkSelfPermission(
                        this@MainActivity, Manifest.permission.ACCESS_COARSE_LOCATION
                    ) == PackageManager.PERMISSION_GRANTED
                if (!granted) {
                    locationPermLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
                }
                callback?.invoke(origin, granted, false)
            }

            override fun onShowFileChooser(
                wv: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                val intent = params?.createIntent()
                return if (intent != null) {
                    try {
                        fileChooserLauncher.launch(intent)
                        true
                    } catch (e: Exception) {
                        filePathCallback = null
                        callback?.onReceiveValue(null)
                        false
                    }
                } else {
                    filePathCallback = null
                    false
                }
            }
        }

        // 사진 다운로드 — WebView 는 Content-Disposition: attachment 를 자체 처리하지 않으므로
        // 시스템 DownloadManager 로 넘겨 Downloads 폴더에 저장(쿠키 동봉으로 인증 유지).
        setDownloadListener { url, ua, disp, mime, _ ->
            try {
                val cookies = CookieManager.getInstance().getCookie(url).orEmpty()
                val name = URLUtil.guessFileName(url, disp, mime)
                val req = DownloadManager.Request(Uri.parse(url)).apply {
                    if (!ua.isNullOrEmpty()) addRequestHeader("User-Agent", ua)
                    if (cookies.isNotEmpty()) addRequestHeader("Cookie", cookies)
                    setMimeType(mime)
                    setTitle(name)
                    setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                }
                (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                Toast.makeText(this@MainActivity, "↓ $name", Toast.LENGTH_SHORT).show()
            } catch (e: Throwable) {
                Toast.makeText(this@MainActivity, "다운로드 실패: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    /** WebView 안에 머물러야 하는 호스트(앱 + Cloudflare Access + Google OAuth IdP). */
    private fun isInAppHost(host: String?): Boolean {
        val h = (host ?: return false).lowercase()
        return h == "couple.ai-ve.uk" || h.endsWith(".couple.ai-ve.uk") ||
            h.endsWith("cloudflareaccess.com") ||                 // Access 로그인/콜백
            h == "accounts.google.com" || h.endsWith(".google.com") ||
            h.endsWith(".gstatic.com") || h.endsWith(".googleusercontent.com") ||
            h == "challenges.cloudflare.com"                      // Turnstile
    }

    /** 웹 → 네이티브 브리지. window.CoupleNative 로 노출된다.
     *  addJavascriptInterface 는 모든 프레임에 주입되므로(로그인용 google 도메인 포함),
     *  메인 프레임이 우리 도메인일 때만 동작하도록 오리진 가드를 둔다. */
    inner class CoupleBridge {
        private fun onAppOrigin(): Boolean {
            val h = pageHost
            return h == "couple.ai-ve.uk" || (h?.endsWith(".couple.ai-ve.uk") == true)
        }

        /** 콕찌르기·리마인더 등을 시스템 알림으로 띄운다. payload: {title, body, tag}. */
        @JavascriptInterface
        fun notify(json: String) {
            if (!onAppOrigin()) return        // 우리 도메인 페이지에서만 허용
            if (json.isBlank()) return
            val obj = try { JSONObject(json) } catch (_: Throwable) { return }
            val title = obj.optString("title").ifBlank { getString(R.string.app_name) }
            val body = obj.optString("body")
            val tag = obj.optString("tag")
            runOnUiThread { showNotification(title, body, tag) }
        }

        /** 웹이 "지금 앱 안인지" 감지할 때 사용. */
        @JavascriptInterface
        fun isCoupleApp(): Boolean = onAppOrigin()
    }

    private fun showNotification(title: String, body: String, tag: String) {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_notify)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .build()
        // 같은 tag(예: 종류)면 갱신, 없으면 매번 새 알림.
        val id = if (tag.isNotBlank()) tag.hashCode() else System.currentTimeMillis().toInt()
        nm.notify(id, notif)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "콕찌르기·리마인더",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "콕찌르기, 캘린더 리마인더 등 둘 사이 실시간 알림"
                enableLights(true)
                enableVibration(true)
            }
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(ch)
        }
    }

    private fun ensureNotifPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun ensureLocationPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            locationPermLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        // 프로세스 종료에도 로그인 유지되도록 쿠키를 디스크로 flush.
        try { CookieManager.getInstance().flush() } catch (_: Throwable) {}
    }
}
