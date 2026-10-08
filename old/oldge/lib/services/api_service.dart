part of '../main.dart';

class ApiService {
  static Future<List<Map<String, String>>> fetchCompanies() async {
    final response = await http
        .get(Uri.parse('$kBaseUrl/api/driver/companies/'))
        .timeout(const Duration(seconds: 12));
    final data = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode == 200 && data['ok'] == true) {
      return List<Map<String, dynamic>>.from(data['companies'] ?? [])
          .map(
            (item) => {
              'key': (item['key'] ?? '').toString(),
              'name': (item['name'] ?? item['key'] ?? '').toString(),
            },
          )
          .where((item) => item['key']!.isNotEmpty)
          .toList();
    }
    throw Exception(data['message'] ?? '讀取公司清單失敗');
  }

  static Future<Map<String, dynamic>> uploadCleaningPhoto({
    required String driverCode,
    required int day,
    required String routeId,
    required File imageFile,
    required String photoType,
    required String pointKey,
    int? stopSeq,
    String? stopAddress,
    String? stopCounty,
    double? stopLat,
    double? stopLon,
    required double photoLat,
    required double photoLon,
  }) async {
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('$kBaseUrl/api/driver/upload-image/'),
    );
    request.headers.addAll(driverAuthHeaders());
    request.fields.addAll({
      'driver_code': driverCode,
      'company_key': kCompanyKey,
      'day': '$day',
      'route_id': routeId,
      'photo_type': photoType,
      'point_key': pointKey,
      'stop_seq': '${stopSeq ?? 1}',
      'stop_address': ?stopAddress,
      'stop_county': ?stopCounty,
      if (stopLat != null) 'stop_lat': '$stopLat',
      if (stopLon != null) 'stop_lon': '$stopLon',
      'photo_lat': '$photoLat',
      'photo_lon': '$photoLon',
    });
    request.files.add(
      await http.MultipartFile.fromPath('image', imageFile.path),
    );
    final response = await http.Response.fromStream(
      await request.send().timeout(const Duration(seconds: 120)),
    ).timeout(const Duration(seconds: 120));
    Map<String, dynamic> data;
    try {
      data = Map<String, dynamic>.from(
        jsonDecode(utf8.decode(response.bodyBytes)),
      );
    } catch (_) {
      throw Exception('照片上傳服務回應異常（HTTP ${response.statusCode}），請確認後端網址與服務狀態');
    }
    if (response.statusCode == 200 && data['ok'] == true) return data;
    throw Exception(data['message'] ?? '照片上傳失敗（HTTP ${response.statusCode}）');
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
    required String companyKey,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/login/'),
      headers: driverAuthHeaders(jsonContent: true),
      body: jsonEncode({
        'driver_code': driverCode,
        'password': password,
        'company_key': companyKey,
      }),
    );

    final data = jsonDecode(utf8.decode(response.bodyBytes));

    if (response.statusCode == 200 && data['success'] == true) {
      kCompanyKey = (data['company_key'] ?? companyKey).toString();
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('company_key', kCompanyKey);
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

  static Future<void> uploadBackgroundLocation({
    required String driverCode,
    required Position position,
    required bool sessionStarted,
  }) async {
    final response = await http.post(
      Uri.parse('$kBaseUrl/api/driver/live/update/'),
      headers: driverAuthHeaders(jsonContent: true),
      body: jsonEncode({
        'driver_code': driverCode,
        'location_only': true,
        'lat': position.latitude,
        'lon': position.longitude,
        'speed_mps': position.speed.isFinite ? position.speed : 0,
        'accuracy_m': position.accuracy,
        'heading': position.heading,
        'session_started': sessionStarted,
        if (BackgroundLocationTracker.instance.activityStatus != null)
          'activity_status': BackgroundLocationTracker.instance.activityStatus,
      }),
    );
    final data = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode != 200 || data['ok'] != true) {
      throw Exception(data['message'] ?? '背景定位上傳失敗');
    }
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
