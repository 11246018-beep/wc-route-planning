part of '../main.dart';

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
                child: Text('讀取失敗：', style: const TextStyle(fontSize: 16)),
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
