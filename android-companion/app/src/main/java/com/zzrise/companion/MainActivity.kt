package com.zzrise.companion

import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.temporal.ChronoUnit

class MainActivity : AppCompatActivity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val http = OkHttpClient()
    private var accessToken: String? = null
    private lateinit var baseUrl: EditText
    private lateinit var email: EditText
    private lateinit var password: EditText
    private lateinit var status: TextView

    private val qrScanner = registerForActivityResult(ScanContract()) { result ->
        if (result.contents.isNullOrBlank()) { showStatus("QR scan cancelled. You can try again.") }
        else { claimPairingCode(result.contents) }
    }

    private val sleepPermissions = setOf(HealthPermission.getReadPermission(SleepSessionRecord::class))
    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) { granted ->
        if (granted.containsAll(sleepPermissions)) syncSleepRecords() else showStatus("Sleep permission was not granted. You can enable it later in Android Health Connect settings.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildScreen()
        val sdkStatus = HealthConnectClient.getSdkStatus(this)
        if (sdkStatus != HealthConnectClient.SDK_AVAILABLE) {
            showStatus("Health Connect is not available on this phone yet. Install or update Health Connect, then reopen this app.")
        }
    }

    private fun buildScreen() {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 32, 24, 24)
            setBackgroundColor(0xFFF7F7FB.toInt())
        }
        val title = TextView(this).apply { text = "☾  ZzzRise Sync"; textSize = 27f; setTextColor(0xFF20243A.toInt()) }
        val intro = TextView(this).apply {
            text = "Connect Samsung Health sleep records to your ZzzRise account through Android Health Connect. Enter your ZzzRise server address, scan the dashboard QR code, then grant sleep permission."
            textSize = 14f; setTextColor(0xFF555B70.toInt()); setPadding(0, 12, 0, 18)
        }
        baseUrl = field("ZzzRise server URL", "https://your-zzrise-domain.example")
        email = field("ZzzRise email", "you@example.com")
        password = field("ZzzRise password", "Your ZzzRise password", true)
        val login = button("1. Sign in with QR code") { scanPairingCode() }
        val manualLogin = button("Or sign in with email and password") { signIn() }
        val connect = button("2. Allow sleep access and sync") { requestSleepPermission() }
        status = TextView(this).apply { text = "Sign in first, then allow sleep access."; textSize = 13f; setTextColor(0xFF39405A.toInt()); setPadding(0, 18, 0, 0) }
        content.addView(title); content.addView(intro)
        content.addView(baseUrl, params()); content.addView(email, params()); content.addView(password, params())
        content.addView(login, params()); content.addView(manualLogin, params()); content.addView(connect, params()); content.addView(status, params())
        val scroll = ScrollView(this).apply { addView(content) }
        setContentView(scroll)
    }

    private fun field(label: String, hintText: String, secret: Boolean = false): EditText {
        return EditText(this).apply {
            hint = "$label · $hintText"
            textSize = 14f
            if (secret) inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            else if (label.contains("email", true)) inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
            setSingleLine(true)
        }
    }

    private fun button(label: String, action: () -> Unit): Button = Button(this).apply { text = label; setOnClickListener { action() } }
    private fun params() = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = 10 }
    private fun showStatus(message: String) { status.text = message }

    private fun server(): String = baseUrl.text.toString().trim().trimEnd('/')

    private fun signIn() {
        if (server().isBlank() || email.text.isBlank() || password.text.isBlank()) {
            showStatus("Enter your ZzzRise server URL, email and password."); return
        }
        showStatus("Signing in…")
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val json = JSONObject().put("email", email.text.toString().trim()).put("password", password.text.toString())
                    val request = Request.Builder().url("${server()}/api/mobile/auth/login")
                        .post(json.toString().toRequestBody("application/json".toMediaType())).build()
                    http.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) throw IllegalStateException(JSONObject(body).optString("detail", "Sign-in failed (${response.code})."))
                        JSONObject(body).getString("access_token")
                    }
                }
                accessToken = result
                password.setText("")
                showStatus("Signed in. Next, allow read access to sleep sessions and sync.")
            } catch (error: Exception) { showStatus(error.message ?: "Could not sign in. Check your server URL and connection.") }
        }
    }

    private fun scanPairingCode() {
        if (server().isBlank()) { showStatus("Enter your ZzzRise server URL first."); return }
        val options = ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt("Scan the QR code shown in your ZzzRise dashboard")
            setBeepEnabled(false)
            setOrientationLocked(false)
        }
        qrScanner.launch(options)
    }

    private fun claimPairingCode(contents: String) {
        val prefix = "ZZRISEPAIR:"
        if (!contents.startsWith(prefix)) { showStatus("That QR code is not a ZzzRise pairing code."); return }
        val pairingToken = contents.removePrefix(prefix).trim()
        showStatus("Connecting this phone to your ZzzRise account…")
        scope.launch {
            try {
                val token = withContext(Dispatchers.IO) {
                    val body = JSONObject().put("pairing_token", pairingToken).toString()
                    val request = Request.Builder().url("${server()}/api/mobile/pairing/claim")
                        .post(body.toRequestBody("application/json".toMediaType())).build()
                    http.newCall(request).execute().use { response ->
                        val responseBody = response.body?.string().orEmpty()
                        if (!response.isSuccessful) throw IllegalStateException(JSONObject(responseBody).optString("detail", "Pairing failed (${response.code})."))
                        JSONObject(responseBody).getString("access_token")
                    }
                }
                accessToken = token
                showStatus("Phone connected to ZzzRise. Next, allow sleep access and sync.")
            } catch (error: Exception) { showStatus(error.message ?: "Could not connect. Generate a fresh QR code and try again.") }
        }
    }

    private fun requestSleepPermission() {
        if (accessToken == null) { showStatus("Please sign in to your ZzzRise account first."); return }
        val client = HealthConnectClient.getOrCreate(this)
        scope.launch {
            try {
                val granted = client.permissionController.getGrantedPermissions()
                if (granted.containsAll(sleepPermissions)) syncSleepRecords() else permissionLauncher.launch(sleepPermissions)
            } catch (error: Exception) { showStatus("Could not check Health Connect permissions: ${error.message}") }
        }
    }

    private fun syncSleepRecords() {
        showStatus("Reading sleep sessions from Health Connect…")
        scope.launch {
            try {
                val client = HealthConnectClient.getOrCreate(this@MainActivity)
                val now = Instant.now()
                val start = now.minus(90, ChronoUnit.DAYS)
                val allRecords = mutableListOf<SleepSessionRecord>()
                var pageToken: String? = null
                do {
                    val page = client.readRecords(
                        ReadRecordsRequest(
                            recordType = SleepSessionRecord::class,
                            timeRangeFilter = TimeRangeFilter.between(start, now),
                            pageToken = pageToken
                        )
                    )
                    allRecords.addAll(page.records)
                    pageToken = page.pageToken
                } while (!pageToken.isNullOrBlank())

                val payloads = allRecords.filter { it.endTime.isAfter(it.startTime) }.map { record ->
                    JSONObject()
                        .put("record_id", record.metadata.id)
                        .put("start_time", record.startTime.toString())
                        .put("end_time", record.endTime.toString())
                        .put("source_app", record.metadata.dataOrigin.packageName)
                }
                var importedTotal = 0
                var skippedTotal = 0
                withContext(Dispatchers.IO) {
                    payloads.chunked(100).forEach { chunk ->
                        val recordsJson = JSONArray()
                        chunk.forEach { recordsJson.put(it) }
                        val body = JSONObject().put("records", recordsJson).toString()
                        val request = Request.Builder().url("${server()}/api/health-connect/sync")
                            .header("Authorization", "Bearer $accessToken")
                            .post(body.toRequestBody("application/json".toMediaType())).build()
                        http.newCall(request).execute().use { httpResponse ->
                            val responseBody = httpResponse.body?.string().orEmpty()
                            if (!httpResponse.isSuccessful) throw IllegalStateException(JSONObject(responseBody).optString("detail", "Sync failed (${httpResponse.code})."))
                            val result = JSONObject(responseBody)
                            importedTotal += result.optInt("imported")
                            skippedTotal += result.optInt("skipped")
                        }
                    }
                }
                showStatus("Sync complete. Imported $importedTotal new session(s); skipped $skippedTotal duplicate or invalid record(s). Return to the ZzzRise website and refresh your dashboard.")
            } catch (error: Exception) { showStatus(error.message ?: "Could not sync sleep records. Check permissions and try again.") }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }
}
