part of '../main.dart';

class BackgroundLocationTracker {
  BackgroundLocationTracker._();

  static final BackgroundLocationTracker instance =
      BackgroundLocationTracker._();

  StreamSubscription<Position>? _positionSubscription;
  DateTime? _lastUploadedAt;
  DateTime? _stationarySince;
  bool _isUploading = false;
  bool _hasUploadedInSession = false;
  String _driverCode = '';
  String? _activityStatus;
  String? _lastError;

  bool get isRunning => _positionSubscription != null;
  String? get activityStatus => _activityStatus;
  String? get lastError => _lastError;

  void setActivityStatus(String? value) {
    _activityStatus = value;
  }

  Future<String?> start(String driverCode) async {
    await stop();
    _driverCode = driverCode.trim().toUpperCase();
    _hasUploadedInSession = false;
    _lastError = null;
    if (_driverCode.isEmpty) return '缺少司機代碼，無法啟動背景定位';

    if (!await Geolocator.isLocationServiceEnabled()) {
      return '手機定位服務尚未開啟，背景定位未啟動';
    }

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return '未取得定位權限，背景定位未啟動';
    }

    late final LocationSettings settings;
    if (Platform.isAndroid) {
      settings = AndroidSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 0,
        intervalDuration: const Duration(seconds: 2),
        foregroundNotificationConfig: const ForegroundNotificationConfig(
          notificationTitle: 'Dispatch Nav 執行勤務中',
          notificationText: '正在回傳司機即時位置',
          enableWakeLock: true,
          setOngoing: true,
        ),
      );
    } else {
      settings = const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 0,
      );
    }

    try {
      final initialPosition = await Geolocator.getCurrentPosition().timeout(
        const Duration(seconds: 15),
      );
      await _handlePosition(initialPosition);
    } catch (e) {
      _lastError = '第一筆定位取得失敗：$e';
    }

    _positionSubscription =
        Geolocator.getPositionStream(locationSettings: settings).listen(
          _handlePosition,
          onError: (error) {
            _lastError = '背景定位服務錯誤：$error';
          },
        );
    return _lastError;
  }

  Future<void> stop() async {
    await _positionSubscription?.cancel();
    _positionSubscription = null;
    _lastUploadedAt = null;
    _stationarySince = null;
    _isUploading = false;
    _hasUploadedInSession = false;
    _driverCode = '';
    _activityStatus = null;
    _lastError = null;
  }

  Future<void> _handlePosition(Position position) async {
    if (_driverCode.isEmpty || kDriverToken == null || _isUploading) return;

    final now = DateTime.now();
    final speedKmh = position.speed.isFinite
        ? (position.speed.clamp(0, double.infinity) * 3.6)
        : 0.0;

    if (speedKmh < 1) {
      _stationarySince ??= now;
    } else {
      _stationarySince = null;
    }

    Duration uploadInterval;
    if (speedKmh >= 1 && speedKmh < 10) {
      uploadInterval = const Duration(seconds: 2);
    } else if (_stationarySince != null &&
        now.difference(_stationarySince!) >= const Duration(seconds: 30)) {
      uploadInterval = const Duration(seconds: 30);
    } else {
      uploadInterval = const Duration(seconds: 5);
    }

    if (_lastUploadedAt != null &&
        now.difference(_lastUploadedAt!) < uploadInterval) {
      return;
    }

    _isUploading = true;
    try {
      await ApiService.uploadBackgroundLocation(
        driverCode: _driverCode,
        position: position,
        sessionStarted: !_hasUploadedInSession,
      );
      _hasUploadedInSession = true;
      _lastError = null;
      _lastUploadedAt = now;
    } catch (e) {
      _lastError = '背景位置上傳失敗：$e';
      // Keep tracking. A later GPS event retries after connectivity recovers.
    } finally {
      _isUploading = false;
    }
  }
}
