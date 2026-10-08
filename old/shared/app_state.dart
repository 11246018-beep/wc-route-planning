part of '../main.dart';

String kBaseUrl = 'https://schools-rivers-sub-lifetime.trycloudflare.com';
String kRouteVariant = 'normal';
String kCompanyKey = 'toilet_demo';
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
  return 'cleaning_progress:$kCompanyKey:$driverCode:$day:$routeId:$routeVariant';
}

String legacyCleaningProgressKeyPrefix({
  required String driverCode,
  required int day,
  required String routeId,
  required String routeVariant,
}) => 'cleaning_progress:$driverCode:$day:$routeId:$routeVariant';

Future<void> migrateLegacyCleaningProgress({
  required SharedPreferences prefs,
  required String driverCode,
  required int day,
  required String routeId,
  required String routeVariant,
}) async {
  // Older releases only served the original company. Preserve its progress
  // while preventing another company with the same route codes from using it.
  if (kCompanyKey != 'toilet_demo') return;

  final currentPrefix = cleaningProgressKeyPrefix(
    driverCode: driverCode,
    day: day,
    routeId: routeId,
    routeVariant: routeVariant,
  );
  final legacyPrefix = legacyCleaningProgressKeyPrefix(
    driverCode: driverCode,
    day: day,
    routeId: routeId,
    routeVariant: routeVariant,
  );
  final migrationKey = '$currentPrefix:legacy_migration_completed';
  if (prefs.getBool(migrationKey) == true) return;

  for (final suffix in const [
    'completed',
    'skipped',
    'before_uploaded',
    'after_uploaded',
  ]) {
    final currentKey = '$currentPrefix:$suffix';
    final legacyValue = prefs.getStringList('$legacyPrefix:$suffix');
    if (!prefs.containsKey(currentKey) && legacyValue != null) {
      await prefs.setStringList(currentKey, legacyValue);
    }
  }
  final currentAckKey = '$currentPrefix:progress_reset_ack';
  final legacyAck = prefs.getString('$legacyPrefix:progress_reset_ack');
  if (!prefs.containsKey(currentAckKey) && legacyAck != null) {
    await prefs.setString(currentAckKey, legacyAck);
  }
  final currentStopKey = '$currentPrefix:current_stop_seq';
  final legacyStop = prefs.getInt('$legacyPrefix:current_stop_seq');
  if (!prefs.containsKey(currentStopKey) && legacyStop != null) {
    await prefs.setInt(currentStopKey, legacyStop);
  }
  await prefs.setBool(migrationKey, true);
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
  BackgroundLocationTracker.instance.stop();
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
