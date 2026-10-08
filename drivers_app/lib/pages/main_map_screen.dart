part of '../main.dart';

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
    await migrateLegacyCleaningProgress(
      prefs: prefs,
      driverCode: widget.driverCode,
      day: day,
      routeId: routeId,
      routeVariant: kRouteVariant,
    );
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
      final resetRequired =
          state['reset_required'] == true && resetAt.isNotEmpty;

      if (!resetRequired) return progress;

      BackgroundLocationTracker.instance.setActivityStatus('idle');
      await clearStoredCleaningProgress(keyPrefix);
      await clearStoredCleaningProgress(
        legacyCleaningProgressKeyPrefix(
          driverCode: widget.driverCode,
          day: day,
          routeId: routeId,
          routeVariant: kRouteVariant,
        ),
      );
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
        progress: (completed: completedStopSeqs, skipped: skippedStopSeqs),
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
