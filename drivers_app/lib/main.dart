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

String kBaseUrl = 'https://schools-rivers-sub-lifetime.trycloudflare.com';
String kRouteVariant = 'normal';

const Map<String, String> kRouteVariantLabels = {
  'normal': '不跨縣市',
  'compact': '可跨縣市',
};

const List<String> kVisibleRouteVariants = ['normal', 'compact'];

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
                'Android 實機請填你電腦的 IPv4。Android 模擬器常用 10.0.2.2，iOS 模擬器常用 127.0.0.1。',
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
                              content: Text('請輸入完整網址，例如 http://172.20.10.2:8000'),
                            ),
                          );
                          return;
                        }
                        kBaseUrl = newUrl.replaceAll(RegExp(r'/+$'), '');
                        Navigator.of(context).pop();
                        await onSaved?.call();
                        if (!context.mounted) return;
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('目前後台已切換為：$kBaseUrl')),
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
        content: const Text('要登出並回到登入頁嗎？'),
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
      title: '司機排程 App',
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
          fillColor: Colors.white.withValues(alpha:0.92),
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
            backgroundColor: Colors.white.withValues(alpha:0.88),
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
  }) async {
    final supabase = Supabase.instance.client;
    final fileExt = imageFile.path.split('.').last.toLowerCase();
    final fileName = const Uuid().v4();
    final filePath =
        '$driverCode/day_$day/$routeId/$photoType/${stopSeq ?? 0}_$fileName.$fileExt';

    await supabase.storage
        .from('photos')
        .upload(filePath, imageFile);

    final publicUrl = supabase.storage
        .from('photos')
        .getPublicUrl(filePath);

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
    });

    return {
      'file_path': filePath,
      'public_url': publicUrl,
      'driver_code': driverCode,
    };
  }

  static Future<Map<String, dynamic>> checkBackend() async {
    final response = await http
        .get(Uri.parse('$kBaseUrl/api/driver/reports/?limit=1'))
        .timeout(const Duration(seconds: 12));

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '後台連線失敗');
  }

  static Future<Map<String, dynamic>> login({
    required String driverCode,
    required String password,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/login/'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'driver_code': driverCode,
        'password': password,
      }),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['success'] == true) {
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

    final response = await http.get(uri);
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '取得排程失敗');
  }

  static Future<Map<String, dynamic>> fetchProfile({
    required String driverCode,
  }) async {
    final uri = Uri.parse(
      '$kBaseUrl/api/driver/profile/?driver_code=$driverCode',
    );

    final response = await http.get(uri);
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data['profile'] ?? {});
    }

    throw Exception(data['message'] ?? '取得個人資料失敗');
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
      headers: {'Content-Type': 'application/json'},
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

    final response = await http.get(uri);
    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return List<Map<String, dynamic>>.from(data['reports'] ?? []);
    }

    throw Exception(data['message'] ?? '取得回報紀錄失敗');
  }

    static Future<Map<String, dynamic>> uploadLiveLocation({
    required String driverCode,
    required int day,
    required String routeId,
    required double lat,
    required double lon,
    required int currentStopSeq,
    required int completedCount,
    required int totalCount,
    required String status,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/live/update/'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'driver_code': driverCode,
        'day': day,
        'route_id': routeId,
        'lat': lat,
        'lon': lon,
        'current_stop_seq': currentStopSeq,
        'completed_count': completedCount,
        'total_count': totalCount,
        'status': status,
      }),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['ok'] == true) {
      return Map<String, dynamic>.from(data);
    }

    throw Exception(data['message'] ?? '上傳位置失敗');
  }

  static Future<Map<String, dynamic>> uploadCleaningImage({
    required String driverCode,
    required File imageFile,
  }) async {
    final uri = Uri.parse('$kBaseUrl/api/driver/upload-image/');
    final request = http.MultipartRequest('POST', uri);

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

    throw Exception(data['message'] ?? '圖片上傳失敗');
  }
  
 
  static Future<Map<String, dynamic>> detectCleaningAI({
    required String driverCode,
    required File imageFile,
    required String photoType,
  }) async {
    final uri = Uri.parse('$kBaseUrl/api/ai/detect/');
    final request = http.MultipartRequest('POST', uri);

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

    throw Exception(data['message'] ?? 'AI辨識失敗');
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
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('請輸入司機編號與密碼')),
      );
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
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('登入失敗：$e')),
      );
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
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('後台連線成功：$kBaseUrl')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('後台連線失敗：$e')),
      );
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
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Card(
              elevation: 2,
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.local_shipping,
                      size: 72,
                      color: Colors.indigo,
                    ),
                    const SizedBox(height: 20),
                    const Text(
                      '司機排程系統',
                      style: TextStyle(
                        fontSize: 28,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      '請輸入司機編號與密碼',
                      style: TextStyle(fontSize: 15, color: Colors.grey),
                    ),
                    const SizedBox(height: 28),
                    TextField(
                      controller: driverIdController,
                      textCapitalization: TextCapitalization.characters,
                      decoration: const InputDecoration(
                        labelText: '司機編號',
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
                                child: CircularProgressIndicator(strokeWidth: 2),
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
                        onPressed: isCheckingConnection ? null : handleTestConnection,
                        icon: isCheckingConnection
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.wifi_tethering),
                        label: const Text('測試後台連線'),
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
                        label: const Text('修改連線設定'),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      '目前後台：$kBaseUrl',
                      style: const TextStyle(fontSize: 12, color: Colors.grey),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '目前模式：${kRouteVariantLabels[kRouteVariant] ?? kRouteVariant}',
                      style: const TextStyle(fontSize: 12, color: Colors.grey),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      '請確認 Base URL 是你目前 Django 可連到的位址',
                      style: TextStyle(fontSize: 12, color: Colors.grey),
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

  @override
  void initState() {
    super.initState();
    loadRouteForDay(selectedDay);
  }

  @override
  void dispose() {
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
      final stops = List<Map<String, dynamic>>.from(route['stops'] ?? []);
      final builtMarkers = <Marker>{};

      for (final stop in stops) {
        final seq = stop['seq']?.toString() ?? '-';
        final lat = _parseDouble(stop['lat'] ?? stop['latitude']);
        final lon = _parseDouble(stop['lon'] ?? stop['lng'] ?? stop['longitude']);
        if (lat == null || lon == null) continue;

        builtMarkers.add(
          Marker(
            markerId: MarkerId('stop_$seq'),
            position: LatLng(lat, lon),
            infoWindow: InfoWindow(
              title: '第 $seq 站',
              snippet: (stop['address'] ?? '未提供地址').toString(),
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

  String _friendlyTaskError(Object error, int day) {
    final raw = error.toString().replaceFirst('Exception: ', '').trim();
    if (raw.contains('找不到') && raw.contains('排程')) {
      final modeLabel = kRouteVariantLabels[kRouteVariant] ?? kRouteVariant;
      return '$raw\n\n這通常不是 app 壞掉，而是司機 ${widget.driverCode} 目前在「$modeLabel」模式下沒有第 $day 天的排程資料。\n請先到 Django 後台重新產生或重新分派排程，再回 app 按右上角重新整理。';
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
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('這個點位沒有座標資料')),
      );
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
        color: Colors.white.withValues(alpha:0.92),
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
            color: Colors.white.withValues(alpha:0.82),
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
                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
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
          '歡迎，司機 ${widget.driverCode}',
          style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 6),
        Text(
          '總部：${widget.depotId} ｜ 工時上限：${widget.maxMinutes} 分',
          style: const TextStyle(fontSize: 14, color: Colors.black54),
        ),
        const SizedBox(height: 4),
        Text(
          '後台：$kBaseUrl',
          style: const TextStyle(fontSize: 12, color: Colors.black45),
        ),
        const SizedBox(height: 14),
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha:0.76),
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: Colors.white.withValues(alpha:0.5)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '第 $selectedDay 天路線摘要',
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              Text('路線編號：${route['route_id'] ?? '-'}'),
              const SizedBox(height: 4),
              Text('模式：${data['label'] ?? (kRouteVariantLabels[kRouteVariant] ?? kRouteVariant)}'),
              const SizedBox(height: 4),
              Text('縣市：${counties.isEmpty ? '-' : counties.join('、')}'),
              const SizedBox(height: 4),
              Text('總站點數：${route['stop_count'] ?? stops.length}'),
              const SizedBox(height: 14),
              Row(
                children: [
                  statBox('總工時', '${fmtNum(metrics['total_min'])} 分', Icons.schedule),
                  const SizedBox(width: 10),
                  statBox('行車時間', '${fmtNum(metrics['drive_min'])} 分', Icons.route),
                  const SizedBox(width: 10),
                  statBox('距離', '${fmtNum(metrics['dist_km'])} km', Icons.straighten),
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
          color: Colors.white.withValues(alpha:0.82),
          borderRadius: BorderRadius.circular(22),
        ),
        child: const Text('今天沒有排程資料'),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          '今日點位清單',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 12),
        ...stops.map((stop) {
          final seq = stop['seq']?.toString() ?? '-';
          final address = stop['address']?.toString() ?? '無地址';
          final county = stop['county']?.toString() ?? '';
          final serviceMin = stop['service_min']?.toString() ?? '0';
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: InkWell(
              borderRadius: BorderRadius.circular(22),
              onTap: () => _focusStop(stop),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha:0.86),
                  borderRadius: BorderRadius.circular(22),
                  border: Border.all(color: const Color(0x14000000)),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 42,
                      height: 42,
                      decoration: const BoxDecoration(
                        color: Colors.black87,
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
                          Text('服務時間：$serviceMin 分'),
                          const SizedBox(height: 8),
                          const Row(
                            children: [
                              Icon(Icons.place_outlined, size: 16, color: Colors.black54),
                              SizedBox(width: 4),
                              Text(
                                '點一下聚焦到地圖',
                                style: TextStyle(fontSize: 12, color: Colors.black54),
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
        Row(
          children: [
            Expanded(
              child: SizedBox(
                height: 52,
                child: OutlinedButton.icon(
                  onPressed: () {
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (context) => SchedulePage(
                          driverCode: widget.driverCode,
                          day: selectedDay,
                        ),
                      ),
                    );
                  },
                  icon: const Icon(Icons.event_note_outlined),
                  label: const Text('詳細排程'),
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: SizedBox(
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
                  label: const Text('問題回報'),
                ),
              ),
            ),
          ],
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
              );
            },
            icon: const Icon(Icons.my_location),
            label: const Text('點位即時更新 / 定位上傳'),
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
                        builder: (context) => DriverProfilePage(
                          driverCode: widget.driverCode,
                        ),
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
                borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
                  child: Container(
                    decoration: BoxDecoration(
                      color: const Color(0xFFE1F5FE).withValues(alpha:0.64),
                      borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
                      border: Border.all(color: Colors.white.withValues(alpha:0.5)),
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
                              color: Colors.black.withValues(alpha:0.12),
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
                          '目前抓取模式：${kRouteVariantLabels[kRouteVariant] ?? kRouteVariant}',
                          style: const TextStyle(fontSize: 12, color: Colors.black54),
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
                              color: Colors.white.withValues(alpha:0.82),
                              borderRadius: BorderRadius.circular(22),
                            ),
                            child: Text('載入失敗：$loadError'),
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

  const DriverProfilePage({
    super.key,
    required this.driverCode,
  });

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
                child: Text('載入失敗：${snapshot.error}'),
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
                        Text('司機編號：${profile['driver_code'] ?? widget.driverCode}'),
                        const SizedBox(height: 6),
                        Text(
                          isActive ? '帳號狀態：啟用中' : '帳號狀態：停用中',
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
                  value: '${profile['max_minutes'] ?? '-'} 分鐘',
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

  const SchedulePage({
    super.key,
    required this.driverCode,
    required this.day,
  });

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
          child: Text(
            '縣市：$county\n任務編號：$taskId\n服務時間：$serviceMin 分鐘',
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
        title: Text('${widget.driverCode} - 第 ${widget.day} 天排程'),
        actions: [
          IconButton(
            onPressed: refreshTask,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      floatingActionButton: FutureBuilder<Map<String, dynamic>>(
        future: futureTask,
        builder: (context, snapshot) {
          final route =
              Map<String, dynamic>.from((snapshot.data ?? {})['route'] ?? {});
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
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('已返回排程頁')),
                        );
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
                  '載入失敗：${snapshot.error}',
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
                        Text('路線編號：${route['route_id'] ?? '-'}'),
                        const SizedBox(height: 4),
                        Text('模式：${data['label'] ?? (kRouteVariantLabels[kRouteVariant] ?? kRouteVariant)}'),
                        const SizedBox(height: 4),
                        Text('縣市：${counties.isEmpty ? '-' : counties.join("、")}'),
                        const SizedBox(height: 4),
                        Text('總站點數：${route['stop_count'] ?? stops.length}'),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    infoCard(
                      '總工時',
                      '${fmtNum(metrics['total_min'])} 分',
                      Icons.schedule,
                    ),
                    infoCard(
                      '行車時間',
                      '${fmtNum(metrics['drive_min'])} 分',
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
                  '今日站點清單',
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
  final TextEditingController currentStopSeqController =
      TextEditingController(text: '1');
  final TextEditingController completedCountController =
      TextEditingController(text: '0');

  Position? currentPosition;
  String selectedStatus = 'navigating';
  bool isLocating = false;
  bool isUploading = false;
  bool autoUploadEnabled = false;
  String lastResultText = '尚未上傳位置';
  String autoUploadText = '自動上傳未啟動';
  Timer? autoUploadTimer;
  final ImagePicker _picker = ImagePicker();
  File? selectedImage;
  bool isUploadingImage = false;
  String uploadImageResult = '尚未上傳清掃照片';
  String selectedPhotoType = 'before';
  bool hasUploadedAfter = false;

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
      return Uri.https(
        'www.google.com',
        '/maps/dir/',
        {
          'api': '1',
          'destination': '$lat,$lon',
          'travelmode': 'driving',
        },
      );
    }

    if (address.isNotEmpty) {
      return Uri.https(
        'www.google.com',
        '/maps/dir/',
        {
          'api': '1',
          'destination': address,
          'travelmode': 'driving',
        },
      );
    }

    return null;
  }

  Future<void> openNavigationToStop(Map<String, dynamic>? stop) async {
    final uri = _buildNavigationUri(stop);

    if (uri == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('這個站點沒有可用的導航資料')),
      );
      return;
    }

    final ok = await launchUrl(
      uri,
      mode: LaunchMode.externalApplication,
    );

    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('無法開啟導航')),
      );
    }
  }

  Future<void> navigateToCurrentStop() async {
    await openNavigationToStop(currentStopData);
  }

  Future<void> navigateToNextStop() async {
    await openNavigationToStop(nextStopData);
  }

  @override
  void dispose() {
    autoUploadTimer?.cancel();
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

  Future<Position?> _fetchCurrentLocation({
    bool showErrorSnackBar = true,
  }) async {
    try {
      final serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        throw Exception('手機定位服務尚未開啟');
      }

      LocationPermission permission = await Geolocator.checkPermission();

      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }

      if (permission == LocationPermission.denied) {
        throw Exception('定位權限被拒絕');
      }

      if (permission == LocationPermission.deniedForever) {
        throw Exception('定位權限被永久拒絕，請到手機設定開啟');
      }

      final pos = await Geolocator.getCurrentPosition();

      if (!mounted) return null;

      setState(() {
        currentPosition = pos;
        lastResultText =
            '已取得目前位置\n'
            '緯度：${pos.latitude}\n'
            '經度：${pos.longitude}\n'
            '精度：約 ${pos.accuracy.toStringAsFixed(1)} 公尺';
      });

      return pos;
    } catch (e) {
      if (!mounted) return null;
      setState(() {
        lastResultText = '取得位置失敗：$e';
      });
      if (showErrorSnackBar) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('取得位置失敗：$e')),
        );
      }
      return null;
    }
  }

  Future<void> getCurrentLocation() async {
    setState(() {
      isLocating = true;
    });

    await _fetchCurrentLocation();

    if (!mounted) return;
    setState(() {
      isLocating = false;
    });
  }

  Future<void> uploadLocation({
    bool silentSuccess = false,
    bool autoMode = false,
  }) async {
    if (isUploading) return;

    Position? pos = currentPosition;

    if (pos == null) {
      if (!mounted) return;
      setState(() {
        isLocating = true;
      });

      pos = await _fetchCurrentLocation(showErrorSnackBar: !autoMode);

      if (!mounted) return;
      setState(() {
        isLocating = false;
      });
    }

    if (pos == null) {
      return;
    }

    final currentSeq = int.tryParse(currentStopSeqController.text.trim()) ?? 0;
    final completed = int.tryParse(completedCountController.text.trim()) ?? 0;

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
        totalCount: widget.totalCount,
        status: selectedStatus,
      );

      final live = Map<String, dynamic>.from(result['live'] ?? {});

      if (!mounted) return;

      setState(() {
        lastResultText =
            '${autoMode ? "自動" : "手動"}上傳成功\n'
            '司機：${live['driver_code'] ?? widget.driverCode}\n'
            '第幾天：${live['day'] ?? widget.day}\n'
            '目前站點：${live['current_stop_seq'] ?? currentSeq}\n'
            '完成數：${live['completed_count'] ?? completed} / ${live['total_count'] ?? widget.totalCount}\n'
            '狀態：${live['status'] ?? selectedStatus}\n'
            '更新時間：${live['updated_at'] ?? '-'}';
      });

      if (!silentSuccess) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(autoMode ? '自動上傳完成' : '即時位置已上傳')),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        lastResultText = '${autoMode ? "自動" : "手動"}上傳失敗：$e';
      });
      if (!autoMode) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('上傳失敗：$e')),
        );
      }
    } finally {
      if (mounted) {
        setState(() {
          isUploading = false;
        });
      }
    }
  }

  Future<void> _autoUploadCycle() async {
    if (!autoUploadEnabled) return;

    if (!mounted) return;
    setState(() {
      autoUploadText = '自動上傳中...';
    });

    await uploadLocation(
      silentSuccess: true,
      autoMode: true,
    );

    if (!mounted) return;
    setState(() {
      autoUploadText = '自動上傳啟動中，每 10 秒更新一次';
    });
  }

  void toggleAutoUpload(bool value) {
    if (value) {
      autoUploadTimer?.cancel();
      setState(() {
        autoUploadEnabled = true;
        autoUploadText = '自動上傳啟動中，每 10 秒更新一次';
      });

      _autoUploadCycle();

      autoUploadTimer = Timer.periodic(const Duration(seconds: 10), (_) {
        _autoUploadCycle();
      });
    } else {
      autoUploadTimer?.cancel();
      setState(() {
        autoUploadEnabled = false;
        autoUploadText = '自動上傳未啟動';
      });
    }
  }

  Future<void> completeCurrentStopAndUpload() async {
    if (widget.totalCount <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('這條路線沒有站點資料')),
      );
      return;
    }

    int current = currentStopSeq;
    int completed = completedCount;

    if (current <= 0) current = 1;
    if (completed < 0) completed = 0;

    int newCompleted = completed;
    if (current > completed) {
      newCompleted = current;
    } else {
      newCompleted = completed + 1;
    }

    if (newCompleted > widget.totalCount) {
      newCompleted = widget.totalCount;
    }

    final isFinished = newCompleted >= widget.totalCount;
    final newCurrent = isFinished ? widget.totalCount : (newCompleted + 1);

    setProgress(
      newCurrentStopSeq: newCurrent,
      newCompletedCount: newCompleted,
      newStatus: isFinished ? 'finished' : 'working',
    );

    setState(() {
      selectedImage = null;
      uploadImageResult = '尚未上傳清掃照片';
      selectedPhotoType = 'before';
      hasUploadedAfter = false;
    });

    await uploadLocation();
  }

  Future<void> completeCurrentStopAndNavigateNext() async {
    if (widget.totalCount <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('這條路線沒有站點資料')),
      );
      return;
    }

    int current = currentStopSeq;
    int completed = completedCount;

    if (current <= 0) current = 1;
    if (completed < 0) completed = 0;

    int newCompleted = completed;
    if (current > completed) {
      newCompleted = current;
    } else {
      newCompleted = completed + 1;
    }

    if (newCompleted > widget.totalCount) {
      newCompleted = widget.totalCount;
    }

    final isFinished = newCompleted >= widget.totalCount;
    final newCurrent = isFinished ? widget.totalCount : (newCompleted + 1);

    setProgress(
      newCurrentStopSeq: newCurrent,
      newCompletedCount: newCompleted,
      newStatus: isFinished ? 'finished' : 'navigating',
    );

    setState(() {
      selectedImage = null;
      uploadImageResult = '尚未上傳清掃照片';
      selectedPhotoType = 'before';
      hasUploadedAfter = false;
    });

    await uploadLocation();

    if (!isFinished && mounted) {
      await openNavigationToStop(currentStopData);
    }
  }

  Future<void> markFinishedAndUpload() async {
    if (widget.totalCount <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('這條路線沒有站點資料')),
      );
      return;
    }

    setProgress(
      newCurrentStopSeq: widget.totalCount,
      newCompletedCount: widget.totalCount,
      newStatus: 'finished',
    );

    await uploadLocation();
  }

  void showAiResultDialog(String message) {
    showDialog(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('AI 辨識結果'),
          content: SingleChildScrollView(
            child: Text(message),
          ),
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
    try {
      final XFile? pickedFile = await _picker.pickImage(
        source: ImageSource.camera,
      );

      if (pickedFile == null) return;

      setState(() {
        selectedImage = File(pickedFile.path);
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('拍照失敗：$e')),
      );
    }
  }

  Future<void> uploadCleaningImage() async {
    if (selectedImage == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('請先拍照')),
      );
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

      final result = await ApiService.detectCleaningAI(
        driverCode: widget.driverCode,
        imageFile: selectedImage!,
        photoType: selectedPhotoType,
      );

      final isQualified = result['is_qualified'] ?? false;
      final reviewStatus = result['status']?.toString() ?? 'normal';

      final uploadResult = await ApiService.uploadImageToSupabase(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        imageFile: selectedImage!,
        photoType: selectedPhotoType,
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
      );

      if (!mounted) return;

      String dialogMessage = '';

      setState(() {
        if (selectedPhotoType == 'after') {
          hasUploadedAfter = true;
        }

        final photoTypeText = selectedPhotoType == 'before' ? '清潔前' : '清潔後';

        final classCountsRaw = result['class_counts'];
        Map<String, dynamic> detectionMap = {};

        if (classCountsRaw is Map) {
          detectionMap = Map<String, dynamic>.from(classCountsRaw);
        }

        final detectionText = detectionMap.isEmpty ? '無' : detectionMap.toString();

        if (selectedPhotoType == 'before') {
          final isRisk = result['is_risk'] ?? false;
          final reason = result['reason']?.toString() ?? '環境狀況尚可';
          final riskScore = result['risk_score'] ?? 0;

          uploadImageResult =
              '照片類型：$photoTypeText\n'
              '上傳者：${uploadResult['driver_code']}\n\n'
              'AI辨識完成\n'
              '風險分數：$riskScore\n'
              '點位風險：${isRisk ? "是" : "否"}\n'
              '原因：$reason\n'
              '辨識結果：$detectionText';

          dialogMessage = uploadImageResult;
        } else {
          final reviewStatus = result['status']?.toString() ?? '未知';

          uploadImageResult =
              '照片類型：$photoTypeText\n'
              '上傳者：${uploadResult['driver_code']}\n\n'
              'AI辨識完成\n'
              '清潔狀態：$reviewStatus\n'
              '辨識結果：$detectionText';

          dialogMessage = uploadImageResult;
        }
      });

      if (!mounted) return;
      showAiResultDialog(dialogMessage);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        uploadImageResult = '上傳或辨識失敗：$e';
      });
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
        : '第 ${stop['seq'] ?? '-'} 站\n'
            '${stop['address'] ?? '無地址'}';

    return Card(
      child: ListTile(
        leading: Icon(icon, color: Colors.indigo),
        title: Text(title),
        subtitle: Text(text),
        isThreeLine: true,
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
      appBar: AppBar(
        title: const Text('即時定位上傳'),
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
                      const SizedBox(height: 8),
                      Text('第 ${widget.day} 天'),
                      const SizedBox(height: 4),
                      Text('路線編號：${widget.routeId.isEmpty ? "-" : widget.routeId}'),
                      const SizedBox(height: 4),
                      Text('總站點數：${widget.totalCount}'),
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
                        '今日進度',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text('已完成：$progressText 站（$progressPercent%）'),
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
                stop: nextStopData,
                icon: Icons.navigation_outlined,
              ),
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
                  onPressed: navigateToNextStop,
                  icon: const Icon(Icons.alt_route),
                  label: const Text('導航到下一站'),
                ),
              ),
              const SizedBox(height: 12),
              infoCard(
                title: '目前緯度',
                value: latText,
                icon: Icons.my_location,
              ),
              infoCard(
                title: '目前經度',
                value: lonText,
                icon: Icons.explore_outlined,
              ),
              const SizedBox(height: 12),
              Card(
                child: SwitchListTile(
                  value: autoUploadEnabled,
                  title: const Text('自動上傳位置'),
                  subtitle: Text(autoUploadText),
                  onChanged: (value) {
                    toggleAutoUpload(value);
                  },
                ),
              ),
              const SizedBox(height: 12),
              const SizedBox(height: 24),
              const Text(
                '清掃照片上傳',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: selectedPhotoType,
                decoration: const InputDecoration(
                  labelText: '照片類型',
                  border: OutlineInputBorder(),
                ),
                items: const [
                  DropdownMenuItem(value: 'before', child: Text('清潔前')),
                  DropdownMenuItem(value: 'after', child: Text('清潔後')),
                ],
                onChanged: (value) {
                  setState(() {
                    selectedPhotoType = value ?? 'before';
                  });
                },
              ),
              const SizedBox(height: 12),
              if (selectedImage != null)
                Image.file(selectedImage!, height: 200)
              else
                const Text('尚未拍照'),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: pickCleaningImage,
                child: const Text('拍照'),
              ),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: isUploadingImage ? null : uploadCleaningImage,
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
                      Text(
                        hasUploadedAfter ? '清潔後照片：已完成' : '清潔後照片：未完成',
                      ),
                      const SizedBox(height: 8),
                      Text(
                        uploadImageResult == '尚未上傳清掃照片'
                            ? '尚未上傳清掃照片'
                            : 'AI 辨識完成，請查看彈出結果',
                      ),
                      if (uploadImageResult != '尚未上傳清掃照片') ...[
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
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                initialValue: selectedStatus,
                decoration: const InputDecoration(
                  labelText: '目前狀態',
                  border: OutlineInputBorder(),
                ),
                items: statusOptions.map((status) {
                  return DropdownMenuItem<String>(
                    value: status,
                    child: Text(status),
                  );
                }).toList(),
                onChanged: (value) {
                  setState(() {
                    selectedStatus = value ?? 'working';
                  });
                },
              ),
              const SizedBox(height: 16),
              TextField(
                controller: currentStopSeqController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: '目前第幾站',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: completedCountController,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                  labelText: '已完成站數',
                  border: const OutlineInputBorder(),
                  helperText: '總站數固定為 ${widget.totalCount}',
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: isLocating ? null : getCurrentLocation,
                  icon: const Icon(Icons.gps_fixed),
                  label: isLocating
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('取得目前位置'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: isUploading
                      ? null
                      : () {
                          uploadLocation();
                        },
                  icon: const Icon(Icons.upload),
                  label: isUploading
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('手動上傳目前位置'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed:
                      (isUploading || !hasUploadedAfter) ? null : completeCurrentStopAndUpload,
                  icon: const Icon(Icons.task_alt),
                  label: const Text('完成目前站點並上傳'),
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
                  onPressed:
                      (isUploading || !hasUploadedAfter) ? null : completeCurrentStopAndNavigateNext,
                  icon: const Icon(Icons.near_me),
                  label: const Text('完成目前站點並導航下一站'),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.green,
                    foregroundColor: Colors.white,
                  ),
                  onPressed: (isUploading || currentStopSeq < widget.totalCount)
                      ? null
                      : markFinishedAndUpload,
                  icon: const Icon(Icons.done_all),
                  label: const Text('標記今日完成'),
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
        color: Colors.black.withValues(alpha:0.45),
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
                          color: Colors.cyanAccent.withValues(alpha:0.75),
                          blurRadius: glow,
                          spreadRadius: 2,
                        ),
                        BoxShadow(
                          color: Colors.blueAccent.withValues(alpha:0.35),
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
                '請稍候，正在分析照片內容',
                style: TextStyle(
                  fontSize: 15,
                  color: Colors.white70,
                ),
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

  
  String selectedType = '客戶不在';
  bool isSubmitting = false;
  bool isLoadingReports = true;
  List<Map<String, dynamic>> reports = [];

  final List<String> reportTypes = const [
    '客戶不在',
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
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('載入回報紀錄失敗：$e')),
      );
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
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('請填寫回報內容')),
      );
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

      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('工作回報已送出')),
      );

      await loadReports();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('送出失敗：$e')),
      );
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
        leading: const Icon(Icons.assignment_turned_in_outlined, color: Colors.indigo),
        title: Text(report['report_type']?.toString() ?? '未分類'),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text(
            '內容：${report['content'] ?? ''}\n'
            '第幾天：${report['day'] ?? '-'} ｜ '
            '第幾站：${report['stop_seq'] ?? '-'}\n'
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
          IconButton(
            onPressed: loadReports,
            icon: const Icon(Icons.refresh),
          ),
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
                        style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 6),
                      Text('第 ${widget.day} 天'),
                      const SizedBox(height: 4),
                      Text('路線編號：${widget.routeId.isEmpty ? "-" : widget.routeId}'),
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
                  labelText: '第幾站（可不填）',
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
                  hintText: '例如：客戶不在現場，電話未接通，已先拍照回報。',
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
                '最近回報紀錄',
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