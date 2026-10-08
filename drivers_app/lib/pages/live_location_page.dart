part of '../main.dart';

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
    await migrateLegacyCleaningProgress(
      prefs: prefs,
      driverCode: widget.driverCode,
      day: widget.day,
      routeId: widget.routeId,
      routeVariant: kRouteVariant,
    );
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
            _isStopUnlocked(
              savedCurrent!,
              completed: completed,
              skipped: skipped,
            )
        ? savedCurrent
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

  bool _isStopUnlocked(int seq, {Set<int>? completed, Set<int>? skipped}) {
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
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請先完成或跳過前一站，才能開放後續站點')));
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

    if (mounted) {
      setState(() {
        selectedStatus = 'navigating';
      });
    }
    BackgroundLocationTracker.instance.setActivityStatus('navigating');

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
    BackgroundLocationTracker.instance.setActivityStatus('idle');
    await clearStoredCleaningProgress(_progressKeyPrefix);
    await clearStoredCleaningProgress(
      legacyCleaningProgressKeyPrefix(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        routeVariant: kRouteVariant,
      ),
    );
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
      final resetRequired =
          state['reset_required'] == true && resetAt.isNotEmpty;

      if (resetRequired) {
        await _applyRemoteProgressReset(resetAt);
        if (!silent && mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(const SnackBar(content: Text('管理端已清除目前進度，APP 已同步歸零')));
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
          : (navigateNext ? 'navigating' : 'idle');
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
      selectedStatus = 'idle';
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
        selectedStatus = 'working';
        authorizedPhotoPosition = null;
        authorizedPhotoType = null;
      });
    }
    BackgroundLocationTracker.instance.setActivityStatus(selectedStatus);

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
        completedStopSeqs: serializeSeqSet(
          completedStopSeqs,
        ).map((value) => int.parse(value)).toList(),
        skippedStopSeqs: serializeSeqSet(
          skippedStopSeqs,
        ).map((value) => int.parse(value)).toList(),
        totalCount: widget.totalCount,
        status: selectedStatus,
        progressResetAck: progressResetAck,
      );

      final live = Map<String, dynamic>.from(result['live'] ?? {});
      final resetAt = '${result['reset_progress_at'] ?? ''}';
      final resetRequired =
          result['reset_required'] == true && resetAt.isNotEmpty;

      if (!mounted) return;

      if (resetRequired) {
        await _applyRemoteProgressReset(resetAt);
        if (!mounted) return;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('管理端已清除目前進度，APP 已同步歸零')));
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

      final uploadResult = await ApiService.uploadCleaningPhoto(
        driverCode: widget.driverCode,
        day: widget.day,
        routeId: widget.routeId,
        imageFile: selectedImage!,
        photoType: uploadingPhotoType,
        pointKey: '${widget.routeId}_$stopSeq',
        stopSeq: stopSeq,
        stopAddress: stopAddress,
        stopCounty: stopCounty,
        stopLat: stopLat,
        stopLon: stopLon,
        photoLat: photoPosition.latitude,
        photoLon: photoPosition.longitude,
      );
      final result = uploadResult;

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
          detectionMap = Map<String, dynamic>.fromEntries(
            Map<String, dynamic>.from(classCountsRaw).entries.where(
              (entry) => const {
                'overflow_bin',
                'bottle',
                'toiletpaper',
              }.contains(entry.key),
            ),
          );
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
