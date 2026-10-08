part of '../main.dart';

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
