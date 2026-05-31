import 'dart:async';
import 'dart:convert';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import 'dart:io';
import 'package:image_picker/image_picker.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:uuid/uuid.dart';
import 'package:shared_preferences/shared_preferences.dart';

String kBaseUrl = 'https://schools-rivers-sub-lifetime.trycloudflare.com';
String kRouteVariant = 'normal';
String? kDriverToken;

const Map<String, String> kRouteVariantLabels = {
  'normal': '不跨縣市路線',
  'compact': '跨縣市路線',
};

const List<String> kVisibleRouteVariants = ['normal', 'compact'];

Set<int> parseStoredSeqSet(List<String>? values) {
  if (values == null) return <int>{};
  return values.map((value) => int.tryParse(value)).whereType<int>().toSet();
}

List<String> serializeSeqSet(Set<int> values) {
  final sorted = values.toList()..sort();
  return sorted.map((value) => value.toString()).toList();
}

String cleaningProgressKeyPrefix({
  required String driverCode,
  required int day,
  required String routeId,
  required String routeVariant,
}) {
  return 'cleaning_progress:$driverCode:$day:$routeId:$routeVariant';
}

Future<void> clearStoredCleaningProgress(String keyPrefix) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.remove('$keyPrefix:current_stop_seq');
  await prefs.remove('$keyPrefix:completed');
  await prefs.remove('$keyPrefix:skipped');
  await prefs.remove('$keyPrefix:before_uploaded');
  await prefs.remove('$keyPrefix:after_uploaded');
}

Map<String, String> driverAuthHeaders({bool jsonContent = false}) {
  return {
    if (jsonContent) 'Content-Type': 'application/json',
    if (kDriverToken != null && kDriverToken!.isNotEmpty)
      'Authorization': 'Bearer $kDriverToken',
  };
}

Future<void> saveDriverToken(String? token) async {
  kDriverToken = token;
  final prefs = await SharedPreferences.getInstance();
  if (token == null || token.isEmpty) {
    await prefs.remove('driver_auth_token');
  } else {
    await prefs.setString('driver_auth_token', token);
  }
}

Future<void> showConnectionSettingsSheet(
  BuildContext context, {
  FutureOr<void> Function()? onSaved,
}) async {
  final urlController = TextEditingController(text: kBaseUrl);

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (context) {
      return Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 20,
          bottom: MediaQuery.of(context).viewInsets.bottom + 24,
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '連線設定',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              const Text(
                'Android 模擬器可使用 10.0.2.2，實機請輸入電腦的區網 IPv4。',
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: urlController,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'Django Base URL',
                  hintText: '例如：http://172.20.10.2:8000',
                  prefixIcon: Icon(Icons.link),
                ),
              ),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final preset in const [
                    'http://172.20.10.2:8000',
                    'http://10.0.2.2:8000',
                    'http://127.0.0.1:8000',
                  ])
                    ActionChip(
                      label: Text(preset),
                      onPressed: () {
                        urlController.text = preset;
                      },
                    ),
                ],
              ),
              const SizedBox(height: 20),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('取消'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton(
                      onPressed: () async {
                        final newUrl = urlController.text.trim();
                        if (newUrl.isEmpty ||
                            !(newUrl.startsWith('http://') ||
                                newUrl.startsWith('https://'))) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text(
                                '請輸入正確網址，例如：http://172.20.10.2:8000',
                              ),
                            ),
                          );
                          return;
                        }
                        kBaseUrl = newUrl.replaceAll(RegExp(r'/+$'), '');
                        Navigator.of(context).pop();
                        await onSaved?.call();
                        if (!context.mounted) return;
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('Base URL 已更新：$kBaseUrl')),
                        );
                      },
                      child: const Text('儲存'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
    },
  );
}

void goToLoginPage(BuildContext context) {
  saveDriverToken(null);
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute(builder: (_) => const LoginPage()),
    (route) => false,
  );
}

Future<void> confirmLogout(BuildContext context) async {
  final shouldLogout = await showDialog<bool>(
    context: context,
    builder: (context) {
      return AlertDialog(
        title: const Text('確認登出'),
        content: const Text('確定要登出嗎？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('登出'),
          ),
        ],
      );
    },
  );

  if (shouldLogout == true && context.mounted) {
    goToLoginPage(context);
  }
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  kDriverToken = prefs.getString('driver_auth_token');

  await Supabase.initialize(
    url: 'https://evwzonunmjvulzitxjmn.supabase.co',
    anonKey: 'sb_publishable_eDjLIB4zVls0zm1ImfPYCA_cD9UAIfd',
  );

  runApp(const DriverApp());
}

class DriverApp extends StatelessWidget {
  const DriverApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Dispatch Nav',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: Colors.grey,
          primary: Colors.black87,
          surface: Colors.white,
        ),
        scaffoldBackgroundColor: const Color(0xFFF2F6F8),
        cardTheme: const CardThemeData(
          elevation: 0,
          margin: EdgeInsets.zero,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(24)),
            side: BorderSide(color: Color(0x14000000)),
          ),
          color: Colors.white,
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white.withValues(alpha: 0.92),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: Color(0x22000000)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: Color(0x22000000)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(18),
            borderSide: const BorderSide(color: Colors.black87, width: 1.2),
          ),
        ),
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            elevation: 0,
            backgroundColor: Colors.black87,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
            ),
          ),
        ),
        outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
            foregroundColor: Colors.black87,
            side: const BorderSide(color: Color(0x22000000)),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(18),
            ),
            backgroundColor: Colors.white.withValues(alpha: 0.88),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
          behavior: SnackBarBehavior.floating,
        ),
      ),
      home: const LoginPage(),
    );
  }
}

class ApiService {
  static Future<Map<String, dynamic>> uploadImageToSupabase({
    required String driverCode,
    required int day,
    required String routeId,
    required File imageFile,
    required String photoType, // before / after
    required bool isQualified,
    required String reviewStatus,
    required String pointKey,
    int? stopSeq,
    required bool? isRisk,
    required int? riskScore,
    required String? riskReason,
    String? stopAddress,
    String? stopCounty,
    double? stopLat,
    double? stopLon,
    required double photoLat,
    required double photoLon,
  }) async {
    final supabase = Supabase.instance.client;
    final fileExt = imageFile.path.split('.').last.toLowerCase();
    final fileName = const Uuid().v4();
    final filePath =
        '$driverCode/day_$day/$routeId/$photoType/${stopSeq ?? 0}_$fileName.$fileExt';

    await supabase.storage.from('photos').upload(filePath, imageFile);

    final publicUrl = supabase.storage.from('photos').getPublicUrl(filePath);

    await supabase.from('uploaded_photos').insert({
      'driver_code': driverCode,
      'day': day,
      'route_id': routeId,
      'stop_seq': stopSeq,
      'photo_type': photoType,
      'point_key': pointKey,
      'file_path': filePath,
      'public_url': publicUrl,
      'is_qualified': isQualified,
      'review_status': reviewStatus,
      'is_risk': isRisk,
      'risk_score': riskScore,
      'risk_reason': riskReason,
      'stop_address': stopAddress,
      'stop_county': stopCounty,
      'stop_lat': stopLat,
      'stop_lon': stopLon,
      'photo_lat': photoLat,
      'photo_lon': photoLon,
      'point_lat': stopLat,
      'point_lon': stopLon,
      'created_at': DateTime.now().toUtc().toIso8601String(),
    });

    return {
      'file_path': filePath,
      'public_url': publicUrl,
      'driver_code': driverCode,
    };
  }

  static Future<Map<String, dynamic>> checkBackend() async {
    final response = await http
        .get(Uri.parse('$kBaseUrl/api/health/'))
        .timeout(const Duration(seconds: 12));

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '連線測試失敗');
  }

  static Future<Map<String, dynamic>> login({
    required String driverCode,
    required String password,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/login/'),
      headers: driverAuthHeaders(jsonContent: true),
      body: jsonEncode({'driver_code': driverCode, 'password': password}),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['success'] == true) {
      await saveDriverToken(data['token']?.toString());
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '登入失敗');
  }

  static Future<Map<String, dynamic>> fetchTask({
    required String driverCode,
    required int day,
  }) async {
    final uri = Uri.parse(
      '$kBaseUrl/api/driver/task/?driver_code=$driverCode&day=$day&variant=$kRouteVariant',
    );

    final response = await http.get(uri, headers: driverAuthHeaders());
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '讀取排程失敗');
  }

  static Future<Map<String, dynamic>> fetchProfile({
    required String driverCode,
  }) async {
    final uri = Uri.parse(
      '$kBaseUrl/api/driver/profile/?driver_code=$driverCode',
    );

    final response = await http.get(uri, headers: driverAuthHeaders());
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data['profile'] ?? {});
    }

    throw Exception(data['message'] ?? '讀取司機資料失敗');
  }

  static Future<Map<String, dynamic>> submitReport({
    required String driverCode,
    required int day,
    required String routeId,
    required String reportType,
    required String content,
    required int stopSeq,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/report/'),
      headers: driverAuthHeaders(jsonContent: true),
      body: jsonEncode({
        'driver_code': driverCode,
        'day': day,
        'route_id': routeId,
        'variant': kRouteVariant,
        'report_type': reportType,
        'content': content,
        'stop_seq': stopSeq,
      }),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '送出回報失敗');
  }

  static Future<List<Map<String, dynamic>>> fetchReports({
    required String driverCode,
  }) async {
    final uri = Uri.parse(
      '$kBaseUrl/api/driver/reports/?driver_code=$driverCode&limit=10',
    );

    final response = await http.get(uri, headers: driverAuthHeaders());
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return List<Map<String, dynamic>>.from(data['reports'] ?? []);
    }

    throw Exception(data['message'] ?? '讀取回報失敗');
  }

  static Future<Map<String, dynamic>> uploadLiveLocation({
    required String driverCode,
    required int day,
    required String routeId,
    required double lat,
    required double lon,
    required int currentStopSeq,
    required int completedCount,
    required List<int> completedStopSeqs,
    required List<int> skippedStopSeqs,
    required int totalCount,
    required String status,
    String? progressResetAck,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/live/update/'),
      headers: driverAuthHeaders(jsonContent: true),
      body: jsonEncode({
        'driver_code': driverCode,
        'day': day,
        'route_id': routeId,
        'lat': lat,
        'lon': lon,
        'current_stop_seq': currentStopSeq,
        'completed_count': completedCount,
        'completed_stop_seqs': completedStopSeqs,
        'skipped_stop_seqs': skippedStopSeqs,
        'total_count': totalCount,
        'status': status,
        'progress_reset_ack': progressResetAck ?? '',
      }),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '上傳定位失敗');
  }

  static Future<Map<String, dynamic>> fetchLiveState({
    required String driverCode,
    String? progressResetAck,
  }) async {
    final uri = Uri.parse('$kBaseUrl/api/driver/live/state/').replace(
      queryParameters: {
        'driver_code': driverCode,
        'progress_reset_ack': progressResetAck ?? '',
      },
    );

    final response = await http.get(uri, headers: driverAuthHeaders());
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '讀取即時狀態失敗');
  }

  static Future<Map<String, dynamic>> uploadCleaningImage({
    required String driverCode,
    required File imageFile,
  }) async {
    final uri = Uri.parse('$kBaseUrl/api/driver/upload-image/');
    final request = http.MultipartRequest('POST', uri);
    request.headers.addAll(driverAuthHeaders());

    request.fields['driver_code'] = driverCode;
    request.files.add(
      await http.MultipartFile.fromPath('image', imageFile.path),
    );

    final streamedResponse = await request.send();
    final response = await http.Response.fromStream(streamedResponse);
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '上傳照片失敗');
  }

  static Future<Map<String, dynamic>> detectCleaningAI({
    required String driverCode,
    required File imageFile,
    required String photoType,
  }) async {
    final uri = Uri.parse('$kBaseUrl/api/ai/detect/');
    final request = http.MultipartRequest('POST', uri);
    request.headers.addAll(driverAuthHeaders());

    request.fields['driver_code'] = driverCode;
    request.fields['photo_type'] = photoType;
    request.files.add(
      await http.MultipartFile.fromPath('image', imageFile.path),
    );

    final streamedResponse = await request.send();
    final response = await http.Response.fromStream(streamedResponse);
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? 'AI 辨識失敗');
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final TextEditingController driverIdController = TextEditingController();
  final TextEditingController passwordController = TextEditingController();

  bool isLoading = false;
  bool isCheckingConnection = false;

  @override
  void dispose() {
    driverIdController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  Future<void> handleLogin() async {
    final driverCode = driverIdController.text.trim().toUpperCase();
    final password = passwordController.text.trim();

    if (driverCode.isEmpty || password.isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請輸入司機代碼與密碼')));
      return;
    }

    setState(() {
      isLoading = true;
    });

    try {
      final data = await ApiService.login(
        driverCode: driverCode,
        password: password,
      );

      if (!mounted) return;

      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (context) => MainMapScreen(
            driverCode: data['driver_code'] ?? driverCode,
            depotId: data['depot_id']?.toString() ?? '',
            maxMinutes: (data['max_minutes'] ?? 540).toString(),
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('登入失敗：$e')));
    } finally {
      if (mounted) {
        setState(() {
          isLoading = false;
        });
      }
    }
  }

  Future<void> handleTestConnection() async {
    setState(() {
      isCheckingConnection = true;
    });

    try {
      await ApiService.checkBackend();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('連線成功：')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('連線失敗：')));
    } finally {
      if (mounted) {
        setState(() {
          isCheckingConnection = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;

    return Scaffold(
      resizeToAvoidBottomInset: true,
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            return SingleChildScrollView(
              keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
              padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + bottomInset),
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  minHeight: constraints.maxHeight - 24,
                ),
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 420),
                    child: Card(
                      elevation: 2,
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Image.asset(
                              'assets/images/dispatch_nav_logo.png',
                              height: 130,
                              fit: BoxFit.contain,
                              errorBuilder: (context, error, stackTrace) {
                                return const Icon(
                                  Icons.local_shipping,
                                  size: 82,
                                  color: Colors.indigo,
                                );
                              },
                            ),
                            const SizedBox(height: 18),
                            const Text(
                              '司機排程系統',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                fontSize: 28,
                                fontWeight: FontWeight.bold,
                                letterSpacing: 1,
                              ),
                            ),
                            const SizedBox(height: 8),
                            const Text(
                              '請輸入司機代碼與密碼',
                              style: TextStyle(
                                fontSize: 15,
                                color: Colors.grey,
                              ),
                            ),
                            const SizedBox(height: 28),
                            TextField(
                              controller: driverIdController,
                              textCapitalization: TextCapitalization.characters,
                              decoration: const InputDecoration(
                                labelText: '司機代碼',
                                hintText: '例如：P01 / W01',
                                border: OutlineInputBorder(),
                                prefixIcon: Icon(Icons.badge_outlined),
                              ),
                            ),
                            const SizedBox(height: 16),
                            TextField(
                              controller: passwordController,
                              obscureText: true,
                              decoration: const InputDecoration(
                                labelText: '密碼',
                                border: OutlineInputBorder(),
                                prefixIcon: Icon(Icons.lock_outline),
                              ),
                            ),
                            const SizedBox(height: 24),
                            SizedBox(
                              width: double.infinity,
                              height: 52,
                              child: ElevatedButton(
                                onPressed: isLoading ? null : handleLogin,
                                child: isLoading
                                    ? const SizedBox(
                                        width: 22,
                                        height: 22,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          valueColor:
                                              AlwaysStoppedAnimation<Color>(
                                                Colors.white,
                                              ),
                                        ),
                                      )
                                    : const Text(
                                        '登入',
                                        style: TextStyle(fontSize: 18),
                                      ),
                              ),
                            ),
                            const SizedBox(height: 12),
                            SizedBox(
                              width: double.infinity,
                              height: 48,
                              child: OutlinedButton.icon(
                                onPressed: isCheckingConnection
                                    ? null
                                    : handleTestConnection,
                                icon: isCheckingConnection
                                    ? const SizedBox(
                                        width: 18,
                                        height: 18,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      )
                                    : const Icon(Icons.wifi_tethering),
                                label: const Text('測試連線'),
                              ),
                            ),
                            const SizedBox(height: 12),
                            SizedBox(
                              width: double.infinity,
                              height: 48,
                              child: OutlinedButton.icon(
                                onPressed: () async {
                                  await showConnectionSettingsSheet(
                                    context,
                                    onSaved: () {
                                      if (mounted) setState(() {});
                                    },
                                  );
                                },
                                icon: const Icon(Icons.settings_ethernet),
                                label: const Text('連線設定'),
                              ),
                            ),
                            const SizedBox(height: 14),
                            Text(
                              '目前連線：$kBaseUrl',
                              style: const TextStyle(
                                fontSize: 12,
                                color: Colors.grey,
                              ),
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              '目前路線：${kRouteVariantLabels[kRouteVariant] ?? kRouteVariant}',
                              style: const TextStyle(
                                fontSize: 12,
                                color: Colors.grey,
                              ),
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 6),
                            const Text(
                              '請確認 Base URL 指向目前 Django 後台',
                              style: TextStyle(
                                fontSize: 12,
                                color: Colors.grey,
                              ),
                              textAlign: TextAlign.center,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class MainMapScreen extends StatefulWidget {
  final String driverCode;
  final String depotId;
  final String maxMinutes;

  const MainMapScreen({
    super.key,
    required this.driverCode,
    required this.depotId,
    required this.maxMinutes,
  });

  @override
  State<MainMapScreen> createState() => _MainMapScreenState();
}

class _MainMapScreenState extends State<MainMapScreen> {
  GoogleMapController? _mapController;
  static const LatLng _defaultCenter = LatLng(25.0478, 121.5170);

  int selectedDay = 1;
  bool isLoadingRoute = true;
  String? loadError;
  Map<String, dynamic> routeData = const {};
  Set<Marker> markers = <Marker>{};
  Set<int> completedStopSeqs = <int>{};
  Set<int> skippedStopSeqs = <int>{};
  Timer? progressResetTimer;
  bool isCheckingProgressReset = false;

  @override
  void initState() {
    super.initState();
    loadRouteForDay(selectedDay);
    progressResetTimer = Timer.periodic(
      const Duration(seconds: 10),
      (_) => _checkCurrentRouteProgressReset(),
    );
  }

  @override
  void dispose() {
    progressResetTimer?.cancel();
    _mapController?.dispose();
    super.dispose();
  }

  double? _parseDouble(dynamic value) {
    if (value == null) return null;
    return double.tryParse(value.toString());
  }

  Future<void> loadRouteForDay(int day) async {
    setState(() {
      selectedDay = day;
      isLoadingRoute = true;
      loadError = null;
    });

    try {
      final data = await ApiService.fetchTask(
        driverCode: widget.driverCode,
        day: day,
      );
      final route = Map<String, dynamic>.from(data['route'] ?? {});
      final routeId = route['route_id']?.toString() ?? '';
      final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);
      final progress = await _loadStoredProgress(day: day, routeId: routeId);
      final syncedProgress = await _syncRemoteProgressResetForRoute(
        day: day,
        routeId: routeId,
        progress: progress,
      );
      final builtMarkers = <Marker>{};

      for (final stop in stops) {
        final seq = stop['seq']?.toString() ?? '-';
        final lat = _parseDouble(stop['lat'] ?? stop['latitude']);
        final lon = _parseDouble(
          stop['lon'] ?? stop['lng'] ?? stop['longitude'],
        );
        if (lat == null || lon == null) continue;

        builtMarkers.add(
          Marker(
            markerId: MarkerId('stop_$seq'),
            position: LatLng(lat, lon),
            infoWindow: InfoWindow(
              title: '第 $seq 站',
              snippet: (stop['address'] ?? '無地址').toString(),
            ),
            onTap: () => _focusStop(stop),
          ),
        );
      }

      if (builtMarkers.isEmpty) {
        builtMarkers.add(
          const Marker(
            markerId: MarkerId('taipei_center'),
            position: _defaultCenter,
            infoWindow: InfoWindow(title: '目前無可顯示點位'),
          ),
        );
      }

      if (!mounted) return;
      setState(() {
        routeData = data;
        markers = builtMarkers;
        completedStopSeqs = syncedProgress.completed;
        skippedStopSeqs = syncedProgress.skipped;
        isLoadingRoute = false;
      });

      await Future.delayed(const Duration(milliseconds: 120));
      if (!mounted) return;
      await _fitRouteCamera();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        routeData = const {};
        markers = {
          const Marker(
            markerId: MarkerId('taipei_center'),
            position: _defaultCenter,
            infoWindow: InfoWindow(title: '預設地圖中心'),
          ),
        };
        loadError = _friendlyTaskError(e, day);
        isLoadingRoute = false;
      });
    }
  }

  Future<({Set<int> completed, Set<int> skipped})> _loadStoredProgress({
    required int day,
    required String routeId,
  }) async {
    if (routeId.isEmpty) {
      return (completed: <int>{}, skipped: <int>{});
    }
    final prefs = await SharedPreferences.getInstance();
    final keyPrefix = cleaningProgressKeyPrefix(
      driverCode: widget.driverCode,
      day: day,
      routeId: routeId,
      routeVariant: kRouteVariant,
    );
    return (
      completed: parseStoredSeqSet(prefs.getStringList('$keyPrefix:completed')),
      skipped: parseStoredSeqSet(prefs.getStringList('$keyPrefix:skipped')),
    );
  }

  Future<({Set<int> completed, Set<int> skipped})>
      _syncRemoteProgressResetForRoute({
    required int day,
    required String routeId,
    required ({Set<int> completed, Set<int> skipped}) progress,
  }) async {
    if (routeId.isEmpty) return progress;

    final keyPrefix = cleaningProgressKeyPrefix(
      driverCode: widget.driverCode,
      day: day,
      routeId: routeId,
      routeVariant: kRouteVariant,
    );
    final prefs = await SharedPreferences.getInstance();
    final ackKey = '$keyPrefix:progress_reset_ack';
    final ack = prefs.getString(ackKey) ?? '';

    try {
      final state = await ApiService.fetchLiveState(
        driverCode: widget.driverCode,
        progressResetAck: ack,
      );
      final resetAt = '${state['reset_progress_at'] ?? ''}';
      final resetRequired = state['reset_required'] == true && resetAt.isNotEmpty;

      if (!resetRequired) return progress;

      await clearStoredCleaningProgress(keyPrefix);
      await prefs.setString(ackKey, resetAt);
      return (completed: <int>{}, skipped: <int>{});
    } catch (_) {
      return progress;
    }
  }

  bool _sameSeqSet(Set<int> a, Set<int> b) =>
      a.length == b.length && a.every(b.contains);

  Future<void> _checkCurrentRouteProgressReset() async {
    if (isCheckingProgressReset || isLoadingRoute) return;
    final route = Map<String, dynamic>.from(routeData['route'] ?? {});
    final routeId = route['route_id']?.toString() ?? '';
    if (routeId.isEmpty) return;

    isCheckingProgressReset = true;
    try {
      final syncedProgress = await _syncRemoteProgressResetForRoute(
        day: selectedDay,
        routeId: routeId,
        progress: (
          completed: completedStopSeqs,
          skipped: skippedStopSeqs,
        ),
      );

      if (!mounted) return;
      if (!_sameSeqSet(completedStopSeqs, syncedProgress.completed) ||
          !_sameSeqSet(skippedStopSeqs, syncedProgress.skipped)) {
        setState(() {
          completedStopSeqs = syncedProgress.completed;
          skippedStopSeqs = syncedProgress.skipped;
        });
      }
    } finally {
      isCheckingProgressReset = false;
    }
  }

  String _friendlyTaskError(Object error, int day) {
    final raw = error.toString().replaceFirst('Exception: ', '').trim();
    if (raw.contains('沒有') && raw.contains('路線')) {
      final modeLabel = kRouteVariantLabels[kRouteVariant] ?? kRouteVariant;
      return '$raw\n\n請確認司機 ${widget.driverCode} 在第 $day 天是否有 $modeLabel 排程。';
    }
    return raw;
  }

  Future<void> _fitRouteCamera() async {
    if (_mapController == null) return;
    final positions = markers.map((m) => m.position).toList();
    if (positions.isEmpty) return;
    if (positions.length == 1) {
      await _mapController!.animateCamera(
        CameraUpdate.newCameraPosition(
          CameraPosition(target: positions.first, zoom: 15),
        ),
      );
      return;
    }

    double minLat = positions.first.latitude;
    double maxLat = positions.first.latitude;
    double minLng = positions.first.longitude;
    double maxLng = positions.first.longitude;

    for (final p in positions.skip(1)) {
      if (p.latitude < minLat) minLat = p.latitude;
      if (p.latitude > maxLat) maxLat = p.latitude;
      if (p.longitude < minLng) minLng = p.longitude;
      if (p.longitude > maxLng) maxLng = p.longitude;
    }

    await _mapController!.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(
          southwest: LatLng(minLat, minLng),
          northeast: LatLng(maxLat, maxLng),
        ),
        80,
      ),
    );
  }

  Future<void> _focusStop(Map<String, dynamic> stop) async {
    final lat = _parseDouble(stop['lat'] ?? stop['latitude']);
    final lon = _parseDouble(stop['lon'] ?? stop['lng'] ?? stop['longitude']);
    if (lat == null || lon == null || _mapController == null) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('找不到此站點座標')));
      return;
    }

    await _mapController!.animateCamera(
      CameraUpdate.newCameraPosition(
        CameraPosition(target: LatLng(lat, lon), zoom: 17),
      ),
    );
  }

  Widget _buildGlassButton({
    required IconData icon,
    required VoidCallback onTap,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.92),
        shape: BoxShape.circle,
        boxShadow: const [
          BoxShadow(
            blurRadius: 20,
            offset: Offset(0, 10),
            color: Color(0x16000000),
          ),
        ],
      ),
      child: IconButton(
        icon: Icon(icon, color: Colors.black87),
        onPressed: onTap,
      ),
    );
  }

  Widget _dayChip(int day) {
    final active = selectedDay == day;
    return Padding(
      padding: const EdgeInsets.only(right: 10),
      child: ChoiceChip(
        label: Text('第 $day 天'),
        selected: active,
        onSelected: (_) => loadRouteForDay(day),
        selectedColor: Colors.black87,
        labelStyle: TextStyle(
          color: active ? Colors.white : Colors.black87,
          fontWeight: FontWeight.w600,
        ),
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0x22000000)),
        ),
        showCheckmark: false,
      ),
    );
  }

  Widget _variantChip(String variant) {
    final active = kRouteVariant == variant;
    final label = kRouteVariantLabels[variant] ?? variant;

    return Padding(
      padding: const EdgeInsets.only(right: 10),
      child: ChoiceChip(
        label: Text(label),
        selected: active,
        onSelected: (_) {
          if (kRouteVariant == variant) return;
          setState(() {
            kRouteVariant = variant;
            completedStopSeqs = <int>{};
            skippedStopSeqs = <int>{};
          });
          loadRouteForDay(selectedDay);
        },
        selectedColor: Colors.indigo,
        labelStyle: TextStyle(
          color: active ? Colors.white : Colors.black87,
          fontWeight: FontWeight.w600,
        ),
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0x22000000)),
        ),
        showCheckmark: false,
      ),
    );
  }

  Widget _summaryCard(Map<String, dynamic> data) {
    final route = Map<String, dynamic>.from(data['route'] ?? {});
    final metrics = Map<String, dynamic>.from(route['metrics'] ?? {});
    final counties = List<dynamic>.from(route['counties'] ?? []);
    final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);

    String fmtNum(dynamic value) {
      if (value == null) return '0';
      if (value is int) return value.toString();
      if (value is double) return value.toStringAsFixed(1);
      final parsed = double.tryParse(value.toString());
      if (parsed == null) return value.toString();
      return parsed.toStringAsFixed(1);
    }

    Widget statBox(String label, String value, IconData icon) {
      return Expanded(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.82),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: const Color(0x14000000)),
          ),
          child: Column(
            children: [
              Icon(icon, color: Colors.black87),
              const SizedBox(height: 8),
              Text(
                value,
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 16,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                label,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: Colors.black54),
              ),
            ],
          ),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '司機：${widget.driverCode}',
          style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 6),
        Text(
          '場站：${widget.depotId}，工時上限：${widget.maxMinutes} 分鐘',
          style: const TextStyle(fontSize: 14, color: Colors.black54),
        ),
        const SizedBox(height: 4),
        Text(
          '連線：$kBaseUrl',
          style: const TextStyle(fontSize: 12, color: Colors.black45),
        ),
        const SizedBox(height: 14),
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.76),
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: Colors.white.withValues(alpha: 0.5)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '第 $selectedDay 天路線總覽',
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              Text('路線：${route['route_id'] ?? '-'}'),
              const SizedBox(height: 4),
              Text(
                '類型：${data['label'] ?? (kRouteVariantLabels[kRouteVariant] ?? kRouteVariant)}',
              ),
              const SizedBox(height: 4),
              Text('縣市：${counties.isEmpty ? '-' : counties.join('、')}'),
              const SizedBox(height: 4),
              Text('站點數：${route['stop_count'] ?? stops.length}'),
              const SizedBox(height: 14),
              Row(
                children: [
                  statBox(
                    '總時間',
                    '${fmtNum(metrics['total_min'])} 分鐘',
                    Icons.schedule,
                  ),
                  const SizedBox(width: 10),
                  statBox(
                    '行駛時間',
                    '${fmtNum(metrics['drive_min'])} 分鐘',
                    Icons.route,
                  ),
                  const SizedBox(width: 10),
                  statBox(
                    '距離',
                    '${fmtNum(metrics['dist_km'])} km',
                    Icons.straighten,
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _stopList(Map<String, dynamic> data) {
    final route = Map<String, dynamic>.from(data['route'] ?? {});
    final routeId = route['route_id']?.toString() ?? '';
    final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);
    final stopCount = int.tryParse('${route['stop_count'] ?? 0}') ?? 0;

    if (stops.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.82),
          borderRadius: BorderRadius.circular(22),
        ),
        child: const Text('今天沒有排程資料'),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          '站點清單',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 12),
        ...stops.map((stop) {
          final seq = stop['seq']?.toString() ?? '-';
          final seqInt = int.tryParse(seq) ?? 0;
          final isCompleted = completedStopSeqs.contains(seqInt);
          final isSkipped = skippedStopSeqs.contains(seqInt) && !isCompleted;
          final address = stop['address']?.toString() ?? '無地址';
          final county = stop['county']?.toString() ?? '';
          final serviceMin = stop['service_min']?.toString() ?? '0';
          final statusText = isCompleted
              ? '已完成'
              : isSkipped
              ? '已跳過'
              : '未完成';
          final statusColor = isCompleted
              ? Colors.green
              : isSkipped
              ? Colors.grey
              : Colors.grey;
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: InkWell(
              borderRadius: BorderRadius.circular(22),
              onTap: () => _focusStop(stop),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: isCompleted
                      ? Colors.green.shade50.withValues(alpha: 0.92)
                      : Colors.white.withValues(alpha: 0.86),
                  borderRadius: BorderRadius.circular(22),
                  border: Border.all(
                    color: isCompleted
                        ? Colors.green.shade400
                        : const Color(0x14000000),
                  ),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 42,
                      height: 42,
                      decoration: BoxDecoration(
                        color: isCompleted ? Colors.green : Colors.black87,
                        shape: BoxShape.circle,
                      ),
                      alignment: Alignment.center,
                      child: Text(
                        seq,
                        style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            address,
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 15,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text('縣市：$county'),
                          Text('服務時間：$serviceMin 分鐘'),
                          const SizedBox(height: 8),
                          Row(
                            children: [
                              Icon(
                                Icons.place_outlined,
                                size: 16,
                                color: statusColor,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                '$statusText，可點選定位',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: statusColor,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }),
        const SizedBox(height: 10),
        SizedBox(
          width: double.infinity,
          height: 52,
          child: OutlinedButton.icon(
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) => ReportPage(
                    driverCode: widget.driverCode,
                    day: selectedDay,
                    routeId: routeId,
                  ),
                ),
              );
            },
            icon: const Icon(Icons.report_problem_outlined),
            label: const Text('工作回報'),
          ),
        ),
        const SizedBox(height: 10),
        SizedBox(
          width: double.infinity,
          height: 54,
          child: ElevatedButton.icon(
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) => LiveLocationPage(
                    driverCode: widget.driverCode,
                    day: selectedDay,
                    routeId: routeId,
                    totalCount: stopCount,
                    stops: stops,
                  ),
                ),
              ).then((_) => loadRouteForDay(selectedDay));
            },
            icon: const Icon(Icons.my_location),
            label: const Text('即時定位 / 清掃作業'),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final data = routeData;

    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(
            child: GoogleMap(
              onMapCreated: (controller) {
                _mapController = controller;
                _fitRouteCamera();
              },
              initialCameraPosition: const CameraPosition(
                target: _defaultCenter,
                zoom: 12,
              ),
              markers: markers,
              myLocationEnabled: true,
              myLocationButtonEnabled: false,
              zoomControlsEnabled: false,
              mapToolbarEnabled: false,
              compassEnabled: true,
            ),
          ),
          Positioned(
            top: 52,
            left: 18,
            child: Column(
              children: [
                _buildGlassButton(
                  icon: Icons.person_outline,
                  onTap: () {
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (context) =>
                            DriverProfilePage(driverCode: widget.driverCode),
                      ),
                    );
                  },
                ),
                const SizedBox(height: 10),
                _buildGlassButton(
                  icon: Icons.logout,
                  onTap: () => confirmLogout(context),
                ),
              ],
            ),
          ),
          Positioned(
            top: 52,
            right: 18,
            child: Column(
              children: [
                _buildGlassButton(
                  icon: Icons.refresh,
                  onTap: () => loadRouteForDay(selectedDay),
                ),
                const SizedBox(height: 10),
                _buildGlassButton(
                  icon: Icons.center_focus_strong,
                  onTap: _fitRouteCamera,
                ),
                const SizedBox(height: 10),
                _buildGlassButton(
                  icon: Icons.settings,
                  onTap: () async {
                    await showConnectionSettingsSheet(
                      context,
                      onSaved: () async {
                        if (!mounted) return;
                        await loadRouteForDay(selectedDay);
                      },
                    );
                  },
                ),
              ],
            ),
          ),
          DraggableScrollableSheet(
            initialChildSize: 0.40,
            minChildSize: 0.18,
            maxChildSize: 0.88,
            builder: (context, scrollController) {
              return ClipRRect(
                borderRadius: const BorderRadius.vertical(
                  top: Radius.circular(30),
                ),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
                  child: Container(
                    decoration: BoxDecoration(
                      color: const Color(0xFFE1F5FE).withValues(alpha: 0.64),
                      borderRadius: const BorderRadius.vertical(
                        top: Radius.circular(30),
                      ),
                      border: Border.all(
                        color: Colors.white.withValues(alpha: 0.5),
                      ),
                    ),
                    child: ListView(
                      controller: scrollController,
                      padding: const EdgeInsets.fromLTRB(20, 16, 20, 30),
                      children: [
                        Center(
                          child: Container(
                            width: 46,
                            height: 5,
                            decoration: BoxDecoration(
                              color: Colors.black.withValues(alpha: 0.12),
                              borderRadius: BorderRadius.circular(20),
                            ),
                          ),
                        ),
                        const SizedBox(height: 18),
                        SizedBox(
                          height: 40,
                          child: ListView(
                            scrollDirection: Axis.horizontal,
                            children: List.generate(6, (i) => _dayChip(i + 1)),
                          ),
                        ),
                        const SizedBox(height: 12),
                        SizedBox(
                          height: 40,
                          child: ListView(
                            scrollDirection: Axis.horizontal,
                            children: kVisibleRouteVariants
                                .map((variant) => _variantChip(variant))
                                .toList(),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          '目前路線：${kRouteVariantLabels[kRouteVariant] ?? kRouteVariant}',
                          style: const TextStyle(
                            fontSize: 12,
                            color: Colors.black54,
                          ),
                        ),
                        const SizedBox(height: 18),
                        if (isLoadingRoute)
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 50),
                            child: Center(child: CircularProgressIndicator()),
                          )
                        else if (loadError != null)
                          Container(
                            padding: const EdgeInsets.all(18),
                            decoration: BoxDecoration(
                              color: Colors.white.withValues(alpha: 0.82),
                              borderRadius: BorderRadius.circular(22),
                            ),
                            child: Text('讀取失敗：'),
                          )
                        else ...[
                          _summaryCard(data),
                          const SizedBox(height: 18),
                          _stopList(data),
                        ],
                      ],
                    ),
                  ),
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

class DriverProfilePage extends StatefulWidget {
  final String driverCode;

  const DriverProfilePage({super.key, required this.driverCode});

  @override
  State<DriverProfilePage> createState() => _DriverProfilePageState();
}

class _DriverProfilePageState extends State<DriverProfilePage> {
  late Future<Map<String, dynamic>> futureProfile;

  @override
  void initState() {
    super.initState();
    futureProfile = ApiService.fetchProfile(driverCode: widget.driverCode);
  }

  Future<void> refreshProfile() async {
    setState(() {
      futureProfile = ApiService.fetchProfile(driverCode: widget.driverCode);
    });
    await futureProfile;
  }

  Widget infoTile({
    required IconData icon,
    required String title,
    required String value,
  }) {
    return Card(
      child: ListTile(
        leading: Icon(icon, color: Colors.indigo),
        title: Text(title),
        subtitle: Text(value.isEmpty ? '-' : value),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('我的資料'),
        actions: [
          IconButton(
            onPressed: refreshProfile,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            onPressed: () => confirmLogout(context),
            icon: const Icon(Icons.logout),
            tooltip: '登出',
          ),
        ],
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: futureProfile,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }

          if (snapshot.hasError) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text('讀取失敗：'),
              ),
            );
          }

          final profile = snapshot.data ?? {};
          final isActive = profile['is_active'] == true;

          return RefreshIndicator(
            onRefresh: refreshProfile,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          profile['display_name']?.toString().isNotEmpty == true
                              ? profile['display_name'].toString()
                              : widget.driverCode,
                          style: const TextStyle(
                            fontSize: 24,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          '司機代碼：${profile['driver_code'] ?? widget.driverCode}',
                        ),
                        const SizedBox(height: 6),
                        Text(
                          isActive ? '啟用中' : '停用中',
                          style: TextStyle(
                            color: isActive ? Colors.green : Colors.red,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                infoTile(
                  icon: Icons.local_shipping_outlined,
                  title: '場站 depot_id',
                  value: '${profile['depot_id'] ?? '-'}',
                ),
                infoTile(
                  icon: Icons.schedule_outlined,
                  title: '工時上限',
                  value: ' 分鐘',
                ),
                infoTile(
                  icon: Icons.phone_outlined,
                  title: '電話',
                  value: profile['phone']?.toString() ?? '',
                ),
                infoTile(
                  icon: Icons.notes_outlined,
                  title: '備註',
                  value: profile['note']?.toString() ?? '',
                ),
                infoTile(
                  icon: Icons.access_time_outlined,
                  title: '建立時間',
                  value: profile['created_at']?.toString() ?? '',
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class SchedulePage extends StatefulWidget {
  final String driverCode;
  final int day;

  const SchedulePage({super.key, required this.driverCode, required this.day});

  @override
  State<SchedulePage> createState() => _SchedulePageState();
}

class _SchedulePageState extends State<SchedulePage> {
  late Future<Map<String, dynamic>> futureTask;

  @override
  void initState() {
    super.initState();
    futureTask = ApiService.fetchTask(
      driverCode: widget.driverCode,
      day: widget.day,
    );
  }

  Future<void> refreshTask() async {
    setState(() {
      futureTask = ApiService.fetchTask(
        driverCode: widget.driverCode,
        day: widget.day,
      );
    });
    await futureTask;
  }

  String fmtNum(dynamic value) {
    if (value == null) return '0';
    if (value is int) return value.toString();
    if (value is double) return value.toStringAsFixed(1);
    final parsed = double.tryParse(value.toString());
    if (parsed == null) return value.toString();
    return parsed.toStringAsFixed(1);
  }

  Widget infoCard(String title, String value, IconData icon) {
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
          child: Column(
            children: [
              Icon(icon, color: Colors.indigo),
              const SizedBox(height: 8),
              Text(
                value,
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                title,
                style: const TextStyle(fontSize: 12, color: Colors.grey),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget buildStopCard(Map<String, dynamic> stop) {
    final seq = stop['seq']?.toString() ?? '-';
    final address = stop['address']?.toString() ?? '無地址';
    final county = stop['county']?.toString() ?? '';
    final taskId = stop['task_id']?.toString() ?? '';
    final serviceMin = stop['service_min']?.toString() ?? '0';

    return Card(
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: Colors.indigo,
          foregroundColor: Colors.white,
          child: Text(seq),
        ),
        title: Text(
          address,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text('縣市：$county\n任務：$taskId\n服務時間：$serviceMin 分鐘'),
        ),
        isThreeLine: true,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.driverCode} - 第 ${widget.day} 天任務'),
        actions: [
          IconButton(onPressed: refreshTask, icon: const Icon(Icons.refresh)),
        ],
      ),
      floatingActionButton: FutureBuilder<Map<String, dynamic>>(
        future: futureTask,
        builder: (context, snapshot) {
          final route = Map<String, dynamic>.from(
            (snapshot.data ?? {})['route'] ?? {},
          );
          final routeId = route['route_id']?.toString() ?? '';
          final stopCount = int.tryParse('${route['stop_count'] ?? 0}') ?? 0;
          final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);

          return Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              FloatingActionButton.extended(
                heroTag: 'live-location',
                onPressed: snapshot.hasData
                    ? () async {
                        await Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (context) => LiveLocationPage(
                              driverCode: widget.driverCode,
                              day: widget.day,
                              routeId: routeId,
                              totalCount: stopCount,
                              stops: stops,
                            ),
                          ),
                        );
                      }
                    : null,
                icon: const Icon(Icons.my_location),
                label: const Text('即時定位'),
              ),
              const SizedBox(height: 12),
              FloatingActionButton.extended(
                heroTag: 'work-report',
                onPressed: snapshot.hasData
                    ? () async {
                        await Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (context) => ReportPage(
                              driverCode: widget.driverCode,
                              day: widget.day,
                              routeId: routeId,
                            ),
                          ),
                        );

                        if (!context.mounted) return;
                        ScaffoldMessenger.of(
                          context,
                        ).showSnackBar(const SnackBar(content: Text('回報頁已關閉')));
                      }
                    : null,
                icon: const Icon(Icons.report_problem_outlined),
                label: const Text('工作回報'),
              ),
            ],
          );
        },
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: futureTask,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }

          if (snapshot.hasError) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  '讀取失敗：',
                  style: const TextStyle(fontSize: 16),
                ),
              ),
            );
          }

          final data = snapshot.data ?? {};
          final route = Map<String, dynamic>.from(data['route'] ?? {});
          final metrics = Map<String, dynamic>.from(route['metrics'] ?? {});
          final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);
          final counties = List<dynamic>.from(route['counties'] ?? []);

          return RefreshIndicator(
            onRefresh: refreshTask,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '司機：${widget.driverCode}',
                          style: const TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text('第 ${widget.day} 天'),
                        const SizedBox(height: 4),
                        Text('路線：${route['route_id'] ?? '-'}'),
                        const SizedBox(height: 4),
                        Text(
                          '類型：${data['label'] ?? (kRouteVariantLabels[kRouteVariant] ?? kRouteVariant)}',
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '縣市：${counties.isEmpty ? '-' : counties.join('、')}',
                        ),
                        const SizedBox(height: 4),
                        Text('站點數：${route['stop_count'] ?? stops.length}'),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    infoCard(
                      '總時間',
                      '${fmtNum(metrics['total_min'])} 分鐘',
                      Icons.schedule,
                    ),
                    infoCard(
                      '行駛時間',
                      '${fmtNum(metrics['drive_min'])} 分鐘',
                      Icons.route,
                    ),
                    infoCard(
                      '距離',
                      '${fmtNum(metrics['dist_km'])} km',
                      Icons.straighten,
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  '站點清單',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 12),
                if (stops.isEmpty)
                  const Card(
                    child: Padding(
                      padding: EdgeInsets.all(20),
                      child: Text('今天沒有排程資料'),
                    ),
                  )
                else
                  ...stops.map(buildStopCard),
                const SizedBox(height: 100),
              ],
            ),
          );
        },
      ),
    );
  }
}

class LiveLocationPage extends StatefulWidget {
  final String driverCode;
  final int day;
  final String routeId;
  final int totalCount;
  final List<Map<String, dynamic>> stops;

  const LiveLocationPage({
    super.key,
    required this.driverCode,
    required this.day,
    required this.routeId,
    required this.totalCount,
    required this.stops,
  });

  @override
  State<LiveLocationPage> createState() => _LiveLocationPageState();
}

class _LiveLocationPageState extends State<LiveLocationPage> {
  final TextEditingController currentStopSeqController = TextEditingController(
    text: '1',
  );
  final TextEditingController completedCountController = TextEditingController(
    text: '0',
  );

  Position? currentPosition;
  String selectedStatus = 'navigating';
  bool isLocating = false;
  bool isUploading = false;
  String lastResultText = '尚未上傳定位';
  final ImagePicker _picker = ImagePicker();
  File? selectedImage;
  Position? authorizedPhotoPosition;
  String? authorizedPhotoType;
  Position? capturedPhotoPosition;
  String? capturedPhotoType;
  bool isUploadingImage = false;
  String uploadImageResult = '尚未上傳照片';
  String selectedPhotoType = 'before';
  Set<int> completedStopSeqs = <int>{};
  Set<int> skippedStopSeqs = <int>{};
  Set<int> beforeUploadedStopSeqs = <int>{};
  Set<int> afterUploadedStopSeqs = <int>{};
  bool hasUploadedBefore = false;
  bool hasUploadedAfter = false;
  Timer? progressResetTimer;
  String progressResetAck = '';
  bool isCheckingProgressReset = false;

  final List<String> statusOptions = const [
    'idle',
    'navigating',
    'working',
    'paused',
    'finished',
  ];

  int get currentStopSeq =>
      int.tryParse(currentStopSeqController.text.trim()) ?? 0;

  int get completedCount =>
      int.tryParse(completedCountController.text.trim()) ?? 0;

  bool get hasAuthorizedLocationForPhoto =>
      authorizedPhotoPosition != null &&
      authorizedPhotoType == selectedPhotoType;

  String get _progressKeyPrefix => cleaningProgressKeyPrefix(
    driverCode: widget.driverCode,
    day: widget.day,
    routeId: widget.routeId,
    routeVariant: kRouteVariant,
  );

  double get progressRatio {
    if (widget.totalCount <= 0) return 0;
    final ratio = completedCount / widget.totalCount;
    return ratio.clamp(0, 1);
  }

  Map<String, dynamic>? getStopBySeq(int seq) {
    if (seq <= 0) return null;

    for (final stop in widget.stops) {
      final stopSeq = int.tryParse('${stop['seq'] ?? 0}') ?? 0;
      if (stopSeq == seq) {
        return stop;
      }
    }

    if (seq - 1 >= 0 && seq - 1 < widget.stops.length) {
      return widget.stops[seq - 1];
    }

    return null;
  }

  Map<String, dynamic>? get currentStopData => getStopBySeq(currentStopSeq);

  Map<String, dynamic>? get nextStopData {
    if (currentStopSeq >= widget.totalCount) return null;
    return getStopBySeq(currentStopSeq + 1);
  }

  @override
  void initState() {
    super.initState();
    _loadProgress();
    progressResetTimer = Timer.periodic(
      const Duration(seconds: 10),
      (_) => _checkProgressReset(silent: true),
    );
  }

  Future<void> _loadProgress() async {
    final prefs = await SharedPreferences.getInstance();
    final completed = parseStoredSeqSet(
      prefs.getStringList('$_progressKeyPrefix:completed'),
    );
    final skipped = parseStoredSeqSet(
      prefs.getStringList('$_progressKeyPrefix:skipped'),
    );
    final beforeUploaded = parseStoredSeqSet(
      prefs.getStringList('$_progressKeyPrefix:before_uploaded'),
    );
    final afterUploaded = parseStoredSeqSet(
      prefs.getStringList('$_progressKeyPrefix:after_uploaded'),
    );
    final savedResetAck =
        prefs.getString('$_progressKeyPrefix:progress_reset_ack') ?? '';
    final savedCurrent = prefs.getInt('$_progressKeyPrefix:current_stop_seq');
    final initialCurrent =
        _validStopSeq(savedCurrent) &&
            _isStopUnlocked(savedCurrent!, completed: completed, skipped: skipped)
        ? savedCurrent!
        : _firstOpenStopSeq(completed: completed, skipped: skipped);

    if (!mounted) return;
    setState(() {
      completedStopSeqs = completed;
      skippedStopSeqs = skipped;
      beforeUploadedStopSeqs = beforeUploaded;
      afterUploadedStopSeqs = afterUploaded;
      progressResetAck = savedResetAck;
      currentStopSeqController.text = '$initialCurrent';
      completedCountController.text = '${completedStopSeqs.length}';
      _syncCurrentStopPhotoState();
    });
    await _checkProgressReset(silent: true);
  }

  Future<void> _saveProgress() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('$_progressKeyPrefix:current_stop_seq', currentStopSeq);
    await prefs.setStringList(
      '$_progressKeyPrefix:completed',
      serializeSeqSet(completedStopSeqs),
    );
    await prefs.setStringList(
      '$_progressKeyPrefix:skipped',
      serializeSeqSet(skippedStopSeqs),
    );
    await prefs.setStringList(
      '$_progressKeyPrefix:before_uploaded',
      serializeSeqSet(beforeUploadedStopSeqs),
    );
    await prefs.setStringList(
      '$_progressKeyPrefix:after_uploaded',
      serializeSeqSet(afterUploadedStopSeqs),
    );
    if (progressResetAck.isNotEmpty) {
      await prefs.setString(
        '$_progressKeyPrefix:progress_reset_ack',
        progressResetAck,
      );
    }
  }

  bool _validStopSeq(int? seq) => seq != null && getStopBySeq(seq) != null;

  List<int> _sortedStopSeqs() {
    final seqs = widget.stops
        .map((stop) => int.tryParse('${stop['seq'] ?? 0}') ?? 0)
        .where((seq) => seq > 0)
        .toList();
    seqs.sort();
    return seqs;
  }

  int _maxUnlockedStopSeq({Set<int>? completed, Set<int>? skipped}) {
    final completedSet = completed ?? completedStopSeqs;
    final skippedSet = skipped ?? skippedStopSeqs;
    final seqs = _sortedStopSeqs();
    if (seqs.isEmpty) return 0;

    var maxUnlocked = seqs.first;
    for (final seq in seqs) {
      if (seq > maxUnlocked) break;
      if (completedSet.contains(seq) || skippedSet.contains(seq)) {
        final currentIndex = seqs.indexOf(seq);
        if (currentIndex >= 0 && currentIndex + 1 < seqs.length) {
          maxUnlocked = seqs[currentIndex + 1];
        }
      } else {
        break;
      }
    }
    return maxUnlocked;
  }

  bool _isStopUnlocked(
    int seq, {
    Set<int>? completed,
    Set<int>? skipped,
  }) {
    if (!_validStopSeq(seq)) return false;
    return seq <= _maxUnlockedStopSeq(completed: completed, skipped: skipped);
  }

  int _firstOpenStopSeq({Set<int>? completed, Set<int>? skipped}) {
    final completedSet = completed ?? completedStopSeqs;
    final skippedSet = skipped ?? skippedStopSeqs;
    for (final stop in widget.stops) {
      final seq = int.tryParse('${stop['seq'] ?? 0}') ?? 0;
      if (seq > 0 && !completedSet.contains(seq) && !skippedSet.contains(seq)) {
        return seq;
      }
    }
    for (final stop in widget.stops) {
      final seq = int.tryParse('${stop['seq'] ?? 0}') ?? 0;
      if (seq > 0 && !completedSet.contains(seq)) {
        return seq;
      }
    }
    return widget.totalCount > 0 ? 1 : 0;
  }

  int _nextOpenStopSeq(int afterSeq) {
    for (final stop in widget.stops) {
      final seq = int.tryParse('${stop['seq'] ?? 0}') ?? 0;
      if (seq > afterSeq &&
          !completedStopSeqs.contains(seq) &&
          !skippedStopSeqs.contains(seq)) {
        return seq;
      }
    }
    return _firstOpenStopSeq();
  }

  void _clearPendingPhotoState() {
    selectedImage = null;
    authorizedPhotoPosition = null;
    authorizedPhotoType = null;
    capturedPhotoPosition = null;
    capturedPhotoType = null;
    uploadImageResult = '尚未上傳照片';
  }

  void _syncCurrentStopPhotoState() {
    hasUploadedBefore = beforeUploadedStopSeqs.contains(currentStopSeq);
    hasUploadedAfter = afterUploadedStopSeqs.contains(currentStopSeq);
    selectedPhotoType = hasUploadedBefore ? 'after' : 'before';
    completedCountController.text = '${completedStopSeqs.length}';
  }

  Future<void> selectCurrentStop(int seq) async {
    if (!_validStopSeq(seq)) return;
    if (!_isStopUnlocked(seq)) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('請先完成或跳過前一站，才能開放後續站點')),
      );
      return;
    }
    setState(() {
      currentStopSeqController.text = '$seq';
      _clearPendingPhotoState();
      _syncCurrentStopPhotoState();
    });
    await _saveProgress();
  }

  double? _parseDouble(dynamic value) {
    if (value == null) return null;
    return double.tryParse(value.toString());
  }

  Uri? _buildNavigationUri(Map<String, dynamic>? stop) {
    if (stop == null) return null;

    final lat = _parseDouble(stop['lat']);
    final lon = _parseDouble(stop['lon']);
    final address = (stop['address'] ?? '').toString().trim();

    if (lat != null && lon != null) {
      return Uri.https('www.google.com', '/maps/dir/', {
        'api': '1',
        'destination': '$lat,$lon',
        'travelmode': 'driving',
      });
    }

    if (address.isNotEmpty) {
      return Uri.https('www.google.com', '/maps/dir/', {
        'api': '1',
        'destination': address,
        'travelmode': 'driving',
      });
    }

    return null;
  }

  Future<void> openNavigationToStop(Map<String, dynamic>? stop) async {
    final uri = _buildNavigationUri(stop);

    if (uri == null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('目前站點沒有可導航資料')));
      return;
    }

    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);

    if (!ok && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('無法開啟導航')));
    }
  }

  Future<void> navigateToCurrentStop() async {
    await openNavigationToStop(currentStopData);
  }

  Future<void> navigateToNextStop() async {
    await openNavigationToStop(getStopBySeq(_nextOpenStopSeq(currentStopSeq)));
  }

  Future<void> _applyRemoteProgressReset(String resetAt) async {
    if (resetAt.isEmpty || resetAt == progressResetAck) return;

    progressResetAck = resetAt;
    await clearStoredCleaningProgress(_progressKeyPrefix);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('$_progressKeyPrefix:progress_reset_ack', resetAt);

    if (!mounted) return;
    setState(() {
      completedStopSeqs = <int>{};
      skippedStopSeqs = <int>{};
      beforeUploadedStopSeqs = <int>{};
      afterUploadedStopSeqs = <int>{};
      currentStopSeqController.text = widget.totalCount > 0 ? '1' : '0';
      completedCountController.text = '0';
      selectedStatus = 'navigating';
      selectedImage = null;
      authorizedPhotoPosition = null;
      authorizedPhotoType = null;
      capturedPhotoPosition = null;
      capturedPhotoType = null;
      hasUploadedBefore = false;
      hasUploadedAfter = false;
      lastResultText = '管理端已清除目前進度，APP 已同步歸零';
      uploadImageResult = '尚未上傳照片';
    });
  }

  Future<void> _checkProgressReset({bool silent = false}) async {
    if (isCheckingProgressReset) return;
    isCheckingProgressReset = true;

    try {
      final state = await ApiService.fetchLiveState(
        driverCode: widget.driverCode,
        progressResetAck: progressResetAck,
      );
      final resetAt = '${state['reset_progress_at'] ?? ''}';
      final resetRequired = state['reset_required'] == true && resetAt.isNotEmpty;

      if (resetRequired) {
        await _applyRemoteProgressReset(resetAt);
        if (!silent && mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('管理端已清除目前進度，APP 已同步歸零')),
          );
        }
      }
    } catch (_) {
      // Keep the current local progress if the monitor endpoint is temporarily unavailable.
    } finally {
      isCheckingProgressReset = false;
    }
  }

  @override
  void dispose() {
    progressResetTimer?.cancel();
    currentStopSeqController.dispose();
    completedCountController.dispose();
    super.dispose();
  }

  void setProgress({
    required int newCurrentStopSeq,
    required int newCompletedCount,
    String? newStatus,
  }) {
    setState(() {
      currentStopSeqController.text = '$newCurrentStopSeq';
      completedCountController.text = '$newCompletedCount';
      if (newStatus != null) {
        selectedStatus = newStatus;
      }
    });
  }

  Future<void> completeCurrentStop({bool navigateNext = false}) async {
    if (!hasUploadedAfter) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請先完成清潔後照片上傳')));
      return;
    }

    if (widget.totalCount <= 0) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('目前沒有站點資料')));
      return;
    }

    final completedSeq = currentStopSeq <= 0 ? 1 : currentStopSeq;
    final nextSeq = _nextOpenStopSeq(completedSeq);

    setState(() {
      completedStopSeqs.add(completedSeq);
      skippedStopSeqs.remove(completedSeq);
      currentStopSeqController.text = '$nextSeq';
      selectedStatus = completedStopSeqs.length >= widget.totalCount
          ? 'finished'
          : 'working';
      _clearPendingPhotoState();
      _syncCurrentStopPhotoState();
    });
    await _saveProgress();
    await uploadLocation();

    if (navigateNext && mounted && completedSeq != nextSeq) {
      await openNavigationToStop(currentStopData);
    }
  }

  Future<void> skipCurrentStop() async {
    if (widget.totalCount <= 0) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('目前沒有站點資料')));
      return;
    }

    final skippedSeq = currentStopSeq <= 0 ? 1 : currentStopSeq;
    final nextSeq = _nextOpenStopSeq(skippedSeq);

    setState(() {
      skippedStopSeqs.add(skippedSeq);
      currentStopSeqController.text = '$nextSeq';
      selectedStatus = 'working';
      _clearPendingPhotoState();
      _syncCurrentStopPhotoState();
    });
    await _saveProgress();
    await uploadLocation();
  }

  Future<Position?> _fetchCurrentLocation({
    bool showErrorSnackBar = true,
  }) async {
    try {
      final serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        throw Exception('定位服務未開啟');
      }

      LocationPermission permission = await Geolocator.checkPermission();

      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }

      if (permission == LocationPermission.denied) {
        throw Exception('定位權限未開啟');
      }

      if (permission == LocationPermission.deniedForever) {
        throw Exception('定位權限已永久拒絕，請到系統設定開啟定位權限');
      }

      final pos = await Geolocator.getCurrentPosition();

      if (!mounted) return null;

      setState(() {
        currentPosition = pos;
        lastResultText =
            '已取得目前位置\n'
            '緯度：\n'
            '經度：\n'
            '精準度：約  公尺';
      });

      return pos;
    } catch (e) {
      if (!mounted) return null;
      setState(() {
        lastResultText = '取得定位失敗：';
      });
      if (showErrorSnackBar) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('取得定位失敗：')));
      }
      return null;
    }
  }

  Future<void> uploadLocation({
    bool silentSuccess = false,
    bool forceFreshLocation = false,
    bool authorizeForPhoto = false,
  }) async {
    if (isUploading) return;

    if (authorizeForPhoto) {
      setState(() {
        authorizedPhotoPosition = null;
        authorizedPhotoType = null;
      });
    }

    Position? pos = forceFreshLocation ? null : currentPosition;

    if (pos == null) {
      if (!mounted) return;
      setState(() {
        isLocating = true;
      });

      pos = await _fetchCurrentLocation();

      if (!mounted) return;
      setState(() {
        isLocating = false;
      });
    }

    if (pos == null) {
      return;
    }

    final currentSeq = int.tryParse(currentStopSeqController.text.trim()) ?? 0;
    final completed = completedStopSeqs.length;

    setState(() {
      isUploading = true;
    });

    try {
      final result = await ApiService.uploadLiveLocation(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        lat: pos.latitude,
        lon: pos.longitude,
        currentStopSeq: currentSeq,
        completedCount: completed,
        completedStopSeqs: serializeSeqSet(completedStopSeqs)
            .map((value) => int.parse(value))
            .toList(),
        skippedStopSeqs: serializeSeqSet(skippedStopSeqs)
            .map((value) => int.parse(value))
            .toList(),
        totalCount: widget.totalCount,
        status: selectedStatus,
        progressResetAck: progressResetAck,
      );

      final live = Map<String, dynamic>.from(result['live'] ?? {});
      final resetAt = '${result['reset_progress_at'] ?? ''}';
      final resetRequired = result['reset_required'] == true && resetAt.isNotEmpty;

      if (!mounted) return;

      if (resetRequired) {
        await _applyRemoteProgressReset(resetAt);
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('管理端已清除目前進度，APP 已同步歸零')),
        );
        return;
      }

      setState(() {
        if (authorizeForPhoto) {
          authorizedPhotoPosition = pos;
          authorizedPhotoType = selectedPhotoType;
          selectedImage = null;
          capturedPhotoPosition = null;
          capturedPhotoType = null;
        }
        lastResultText =
            '定位上傳成功\n'
            '司機：${live['driver_code'] ?? widget.driverCode}\n'
            '天數：${live['day'] ?? widget.day}\n'
            '目前站點：${live['current_stop_seq'] ?? currentSeq}\n'
            '完成進度：${live['completed_count'] ?? completed} / ${live['total_count'] ?? widget.totalCount}\n'
            '狀態：${live['status'] ?? selectedStatus}\n'
            '更新時間：${live['updated_at'] ?? '-'}';
      });

      if (!silentSuccess) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              authorizeForPhoto
                  ? '${selectedPhotoType == "before" ? "清潔前" : "清潔後"}定位上傳成功，可以拍照'
                  : '即時定位上傳成功',
            ),
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        if (authorizeForPhoto) {
          authorizedPhotoPosition = null;
          authorizedPhotoType = null;
        }
        lastResultText = '上傳定位失敗：';
      });
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('上傳定位失敗，請稍後再試：')));
    } finally {
      if (mounted) {
        setState(() {
          isUploading = false;
        });
      }
    }
  }

  Future<void> completeCurrentStopAndUpload() async {
    await completeCurrentStop();
  }

  Future<void> completeCurrentStopAndNavigateNext() async {
    await completeCurrentStop(navigateNext: true);
  }

  void showAiResultDialog(String message) {
    showDialog(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('AI 辨識結果'),
          content: SingleChildScrollView(child: Text(message)),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('確定'),
            ),
          ],
        );
      },
    );
  }

  Future<void> pickCleaningImage() async {
    if (!hasAuthorizedLocationForPhoto) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            '請先為${selectedPhotoType == "before" ? "清潔前" : "清潔後"}照片上傳當下位置，成功後才能拍照',
          ),
        ),
      );
      return;
    }

    try {
      final XFile? pickedFile = await _picker.pickImage(
        source: ImageSource.camera,
      );

      if (pickedFile == null) return;

      setState(() {
        selectedImage = File(pickedFile.path);
        capturedPhotoPosition = authorizedPhotoPosition;
        capturedPhotoType = authorizedPhotoType;
        authorizedPhotoPosition = null;
        authorizedPhotoType = null;
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('拍照失敗：')));
    }
  }

  Future<void> uploadCleaningImage() async {
    if (selectedImage == null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請先拍照')));
      return;
    }

    if (capturedPhotoPosition == null ||
        capturedPhotoType != selectedPhotoType) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請先上傳當下位置，再拍照上傳')));
      return;
    }

    setState(() {
      isUploadingImage = true;
    });

    try {
      final stopSeq = currentStopSeq;
      final stopData = currentStopData;
      final stopAddress = stopData?['address']?.toString();
      final stopCounty = stopData?['county']?.toString();
      final stopLat = double.tryParse('${stopData?['lat'] ?? ''}');
      final stopLon = double.tryParse('${stopData?['lon'] ?? ''}');
      final photoPosition = capturedPhotoPosition!;
      final uploadingPhotoType = selectedPhotoType;

      final result = await ApiService.detectCleaningAI(
        driverCode: widget.driverCode,
        imageFile: selectedImage!,
        photoType: uploadingPhotoType,
      );

      final isQualified = result['is_qualified'] ?? false;
      final reviewStatus = result['status']?.toString() ?? 'normal';

      final uploadResult = await ApiService.uploadImageToSupabase(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        imageFile: selectedImage!,
        photoType: uploadingPhotoType,
        isQualified: isQualified,
        reviewStatus: reviewStatus,
        pointKey: '${widget.routeId}_$stopSeq',
        stopSeq: stopSeq,
        isRisk: result['is_risk'],
        riskScore: result['risk_score'],
        riskReason: result['reason']?.toString(),
        stopAddress: stopAddress,
        stopCounty: stopCounty,
        stopLat: stopLat,
        stopLon: stopLon,
        photoLat: photoPosition.latitude,
        photoLon: photoPosition.longitude,
      );

      if (!mounted) return;

      String dialogMessage = '';

      setState(() {
        if (uploadingPhotoType == 'before') {
          beforeUploadedStopSeqs.add(stopSeq);
          hasUploadedBefore = true;
          selectedPhotoType = 'after';
        } else {
          afterUploadedStopSeqs.add(stopSeq);
          hasUploadedAfter = true;
        }

        selectedImage = null;
        capturedPhotoPosition = null;
        capturedPhotoType = null;
        authorizedPhotoPosition = null;
        authorizedPhotoType = null;

        final photoTypeText = uploadingPhotoType == 'before' ? '清潔前' : '清潔後';

        final classCountsRaw = result['class_counts'];
        Map<String, dynamic> detectionMap = {};

        if (classCountsRaw is Map) {
          detectionMap = Map<String, dynamic>.from(classCountsRaw);
        }

        final detectionText = detectionMap.isEmpty
            ? '無'
            : detectionMap.toString();

        if (uploadingPhotoType == 'before') {
          final isRisk = result['is_risk'] ?? false;
          final reason = result['reason']?.toString() ?? '未提供原因';

          uploadImageResult =
              '照片類型：$photoTypeText\n'
              '上傳者：${uploadResult['driver_code']}\n\n'
              'AI辨識完成\n'
              '點位風險：${isRisk ? "是" : "否"}\n'
              '原因：$reason\n'
              '辨識結果：$detectionText';

          dialogMessage = uploadImageResult;
        } else {
          final reviewStatus = result['status']?.toString() ?? '未分類';

          uploadImageResult =
              '照片類型：$photoTypeText\n'
              '上傳者：${uploadResult['driver_code']}\n\n'
              'AI辨識完成\n'
              '清潔後狀態：$reviewStatus\n'
              '辨識結果：$detectionText';

          dialogMessage = uploadImageResult;
        }
      });

      if (!mounted) return;
      await _saveProgress();
      if (!mounted) return;
      showAiResultDialog(dialogMessage);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        uploadImageResult = '上傳照片失敗：$e';
      });
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('上傳照片失敗：$e')));
    } finally {
      if (mounted) {
        setState(() {
          isUploadingImage = false;
        });
      }
    }
  }

  Widget infoCard({
    required String title,
    required String value,
    IconData? icon,
  }) {
    return Card(
      child: ListTile(
        leading: icon != null ? Icon(icon, color: Colors.indigo) : null,
        title: Text(title),
        subtitle: Text(value),
      ),
    );
  }

  Widget stopCard({
    required String title,
    required Map<String, dynamic>? stop,
    required IconData icon,
  }) {
    final text = stop == null
        ? '無資料'
        : '第 ${stop['seq'] ?? '-'} 站\n${stop['address'] ?? '無地址'}';


    return Card(
      child: ListTile(
        leading: Icon(icon, color: Colors.indigo),
        title: Text(title),
        subtitle: Text(text),
        isThreeLine: true,
      ),
    );
  }

  Widget stopSelectorCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '選擇目前站點',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            const Text(
              '站點會依序開放；完成或跳過前一站後才可選下一站，已開放的前面站點可回頭補清。',
              style: TextStyle(color: Colors.black54),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final stop in widget.stops)
                  Builder(
                    builder: (context) {
                      final seq = int.tryParse('${stop['seq'] ?? 0}') ?? 0;
                      final active = seq == currentStopSeq;
                      final completed = completedStopSeqs.contains(seq);
                      final skipped =
                          skippedStopSeqs.contains(seq) && !completed;
                      final unlocked = _isStopUnlocked(seq);
                      final selectedColor = completed
                          ? Colors.green
                          : skipped
                          ? Colors.grey
                          : Colors.grey;
                      final backgroundColor = completed
                          ? Colors.green.shade100
                          : skipped
                          ? Colors.grey.shade200
                          : unlocked
                          ? Colors.grey.shade200
                          : Colors.grey.shade100;
                      return ChoiceChip(
                        label: Text('第 $seq 站'),
                        selected: active,
                        selectedColor: selectedColor,
                        backgroundColor: backgroundColor,
                        labelStyle: TextStyle(
                          color: active
                              ? Colors.white
                              : unlocked
                              ? Colors.black87
                              : Colors.black38,
                          fontWeight: FontWeight.w600,
                        ),
                        onSelected: unlocked
                            ? (_) => selectCurrentStop(seq)
                            : null,
                      );
                    },
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final latText = currentPosition == null
        ? '-'
        : currentPosition!.latitude.toStringAsFixed(6);
    final lonText = currentPosition == null
        ? '-'
        : currentPosition!.longitude.toStringAsFixed(6);

    final progressText =
        '${completedCount.clamp(0, widget.totalCount)} / ${widget.totalCount}';
    final progressPercent = (progressRatio * 100).toStringAsFixed(1);

    return Scaffold(
      appBar: AppBar(title: const Text('即時定位上傳')),
      body: Stack(
        children: [
          ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '司機：${widget.driverCode}',
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text('第 ${widget.day} 天'),
                      const SizedBox(height: 4),
                      Text(
                        "路線：${widget.routeId.isEmpty ? '-' : widget.routeId}",
                      ),
                      const SizedBox(height: 4),
                      Text('站點數：${widget.totalCount}'),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '清掃進度',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text('進度：$progressText，$progressPercent%'),
                      const SizedBox(height: 10),
                      LinearProgressIndicator(
                        value: progressRatio,
                        minHeight: 10,
                        borderRadius: BorderRadius.circular(999),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
              stopCard(
                title: '目前站點',
                stop: currentStopData,
                icon: Icons.place_outlined,
              ),
              stopCard(
                title: '下一站',
                stop: getStopBySeq(_nextOpenStopSeq(currentStopSeq)),
                icon: Icons.navigation_outlined,
              ),
              stopSelectorCard(),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: OutlinedButton.icon(
                  onPressed: navigateToCurrentStop,
                  icon: const Icon(Icons.directions),
                  label: const Text('導航到目前站點'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: OutlinedButton.icon(
                  onPressed: hasUploadedAfter ? navigateToNextStop : null,
                  icon: const Icon(Icons.alt_route),
                  label: const Text('導航到下一個未完成站點'),
                ),
              ),
              const SizedBox(height: 12),
              infoCard(title: '目前緯度', value: latText, icon: Icons.my_location),
              infoCard(
                title: '目前經度',
                value: lonText,
                icon: Icons.explore_outlined,
              ),
              const SizedBox(height: 12),
              const SizedBox(height: 24),
              const Text(
                '清掃照片上傳',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              InputDecorator(
                decoration: const InputDecoration(labelText: '清潔前狀態'),
                child: Text(
                  hasUploadedBefore ? '清潔前照片已上傳' : '清潔前照片未完成',
                  style: const TextStyle(fontSize: 16),
                ),
              ),
              const SizedBox(height: 12),
              Text(
                hasAuthorizedLocationForPhoto
                    ? '定位已上傳，可以拍照或上傳照片'
                    : '請先按「上傳當下位置」，成功後才能拍照',
                style: TextStyle(
                  color: hasAuthorizedLocationForPhoto
                      ? Colors.green.shade700
                      : Colors.orange.shade800,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 12),
              if (selectedImage != null)
                Image.file(selectedImage!, height: 200)
              else
                const Text('尚未拍照'),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: hasAuthorizedLocationForPhoto
                    ? pickCleaningImage
                    : null,
                child: Text(
                  selectedPhotoType == 'before' ? '拍清潔前照片' : '拍清潔後照片',
                ),
              ),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: (isUploadingImage || selectedImage == null)
                    ? null
                    : uploadCleaningImage,
                child: isUploadingImage
                    ? const CircularProgressIndicator()
                    : const Text('上傳照片'),
              ),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(hasUploadedBefore ? '清潔前照片已完成' : '清潔前照片未完成'),
                      const SizedBox(height: 8),
                      Text(hasUploadedAfter ? '清潔後照片已完成' : '清潔後照片未完成'),
                      const SizedBox(height: 8),
                      Text(
                        uploadImageResult == '尚未上傳照片'
                            ? '尚未上傳照片'
                            : 'AI 辨識完成，請查看彈出結果',
                      ),
                      if (uploadImageResult != '尚未上傳照片') ...[
                        const SizedBox(height: 12),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: () {
                              showAiResultDialog(uploadImageResult);
                            },
                            child: const Text('查看辨識結果'),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 4),
              const SizedBox(height: 16),
              TextField(
                controller: currentStopSeqController,
                readOnly: true,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: '目前站點',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: completedCountController,
                readOnly: true,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                  labelText: '已完成站點數',
                  border: const OutlineInputBorder(),
                  helperText: '總站點數為 ${widget.totalCount}',
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: (isUploading || isLocating || hasUploadedAfter)
                      ? null
                      : () {
                          uploadLocation(
                            forceFreshLocation: true,
                            authorizeForPhoto: true,
                          );
                        },
                  icon: const Icon(Icons.upload),
                  label: isUploading
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Text(
                          '上傳${selectedPhotoType == "before" ? "清潔前" : "清潔後"}定位',
                        ),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: (isUploading || !hasUploadedAfter)
                      ? null
                      : completeCurrentStopAndUpload,
                  icon: const Icon(Icons.task_alt),
                  label: const Text('完成目前站點'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.deepPurple,
                    foregroundColor: Colors.white,
                  ),
                  onPressed: (isUploading || !hasUploadedAfter)
                      ? null
                      : completeCurrentStopAndNavigateNext,
                  icon: const Icon(Icons.near_me),
                  label: const Text('完成並前往下一站'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.orange,
                    foregroundColor: Colors.white,
                  ),
                  onPressed: isUploading ? null : skipCurrentStop,
                  icon: const Icon(Icons.skip_next),
                  label: const Text('跳過目前站點'),
                ),
              ),
              const SizedBox(height: 24),
              const Text(
                '上傳結果',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(lastResultText),
                ),
              ),
            ],
          ),
          if (isUploadingImage) const AILoadingOverlay(),
        ],
      ),
    );
  }
}

class AILoadingOverlay extends StatefulWidget {
  const AILoadingOverlay({super.key});

  @override
  State<AILoadingOverlay> createState() => _AILoadingOverlayState();
}

class _AILoadingOverlayState extends State<AILoadingOverlay>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: Container(
        color: Colors.black.withValues(alpha: 0.45),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedBuilder(
                animation: _controller,
                builder: (context, child) {
                  final glow = 12 + (_controller.value * 24);

                  return Container(
                    width: 110,
                    height: 110,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: Colors.white,
                      boxShadow: [
                        BoxShadow(
                          color: Colors.cyanAccent.withValues(alpha: 0.75),
                          blurRadius: glow,
                          spreadRadius: 2,
                        ),
                        BoxShadow(
                          color: Colors.blueAccent.withValues(alpha: 0.35),
                          blurRadius: glow + 12,
                          spreadRadius: 6,
                        ),
                      ],
                    ),
                    child: const Icon(
                      Icons.auto_awesome,
                      size: 48,
                      color: Colors.indigo,
                    ),
                  );
                },
              ),
              const SizedBox(height: 24),
              const Text(
                'AI 辨識中...',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  color: Colors.white,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                '請稍候，正在分析照片與回傳結果',
                style: TextStyle(fontSize: 15, color: Colors.white70),
              ),
              const SizedBox(height: 18),
              const SizedBox(
                width: 30,
                height: 30,
                child: CircularProgressIndicator(
                  strokeWidth: 3,
                  color: Colors.white,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class ReportPage extends StatefulWidget {
  final String driverCode;
  final int day;
  final String routeId;

  const ReportPage({
    super.key,
    required this.driverCode,
    required this.day,
    required this.routeId,
  });

  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  final TextEditingController contentController = TextEditingController();
  final TextEditingController stopSeqController = TextEditingController();

  String selectedType = '地址有誤';
  bool isSubmitting = false;
  bool isLoadingReports = true;
  List<Map<String, dynamic>> reports = [];

  final List<String> reportTypes = const [
    '地址有誤',
    '無法進入',
    '設備異常',
    '臨時取消',
    '交通延誤',
    '其他',
  ];

  @override
  void initState() {
    super.initState();
    loadReports();
  }

  @override
  void dispose() {
    contentController.dispose();
    stopSeqController.dispose();
    super.dispose();
  }

  Future<void> loadReports() async {
    setState(() {
      isLoadingReports = true;
    });

    try {
      final data = await ApiService.fetchReports(driverCode: widget.driverCode);
      if (!mounted) return;
      setState(() {
        reports = data;
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('讀取回報紀錄失敗：')));
    } finally {
      if (mounted) {
        setState(() {
          isLoadingReports = false;
        });
      }
    }
  }

  Future<void> submitReport() async {
    final content = contentController.text.trim();
    final stopSeq = int.tryParse(stopSeqController.text.trim()) ?? 0;

    if (content.isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請輸入回報內容')));
      return;
    }

    setState(() {
      isSubmitting = true;
    });

    try {
      await ApiService.submitReport(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        reportType: selectedType,
        content: content,
        stopSeq: stopSeq,
      );

      if (!mounted) return;

      contentController.clear();
      stopSeqController.clear();

      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('工作回報已送出')));

      await loadReports();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('送出失敗：')));
    } finally {
      if (mounted) {
        setState(() {
          isSubmitting = false;
        });
      }
    }
  }

  Widget buildReportCard(Map<String, dynamic> report) {
    return Card(
      child: ListTile(
        leading: const Icon(
          Icons.assignment_turned_in_outlined,
          color: Colors.indigo,
        ),
        title: Text(report['report_type']?.toString() ?? '未分類'),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text(
            '內容：${report['content'] ?? ''}\n'
            '天數：${report['day'] ?? '-'}，'
            '站點：${report['stop_seq'] ?? '-'}\n'
            '時間：${report['created_at'] ?? '-'}',
          ),
        ),
        isThreeLine: true,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('工作回報'),
        actions: [
          IconButton(onPressed: loadReports, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: Stack(
        children: [
          ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '司機：${widget.driverCode}',
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text('第 ${widget.day} 天'),
                      const SizedBox(height: 4),
                      Text(
                        "路線：${widget.routeId.isEmpty ? '-' : widget.routeId}",
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                initialValue: selectedType,
                decoration: const InputDecoration(
                  labelText: '回報類型',
                  border: OutlineInputBorder(),
                ),
                items: reportTypes.map((type) {
                  return DropdownMenuItem<String>(
                    value: type,
                    child: Text(type),
                  );
                }).toList(),
                onChanged: (value) {
                  setState(() {
                    selectedType = value ?? reportTypes.first;
                  });
                },
              ),
              const SizedBox(height: 16),
              TextField(
                controller: stopSeqController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: '站點編號（可留空）',
                  hintText: '例如：3',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: contentController,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: '回報內容',
                  hintText: '請輸入回報內容，例如現場狀況、照片補充或特殊問題。',
                  border: OutlineInputBorder(),
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: isSubmitting ? null : submitReport,
                  icon: const Icon(Icons.send),
                  label: isSubmitting
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('送出工作回報'),
                ),
              ),

              const SizedBox(height: 24),
              const Text(
                '回報紀錄',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              if (isLoadingReports)
                const Center(child: CircularProgressIndicator())
              else if (reports.isEmpty)
                const Card(
                  child: Padding(
                    padding: EdgeInsets.all(20),
                    child: Text('目前沒有回報紀錄'),
                  ),
                )
              else
                ...reports.map(buildReportCard),
            ],
          ),
        ],
      ),
    );
  }
}

