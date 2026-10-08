part of '../main.dart';

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
  bool isLoadingCompanies = true;
  String? companyLoadError;
  String selectedCompanyKey = kCompanyKey;
  List<Map<String, String>> companies = const [];

  @override
  void initState() {
    super.initState();
    loadCompanies();
  }

  @override
  void dispose() {
    driverIdController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  Future<void> loadCompanies() async {
    setState(() {
      isLoadingCompanies = true;
      companyLoadError = null;
    });
    try {
      final result = await ApiService.fetchCompanies();
      if (!mounted) return;
      final hasSavedCompany = result.any((item) => item['key'] == kCompanyKey);
      setState(() {
        companies = result;
        selectedCompanyKey = hasSavedCompany
            ? kCompanyKey
            : (result.isNotEmpty ? result.first['key']! : '');
        isLoadingCompanies = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        companies = const [];
        selectedCompanyKey = '';
        companyLoadError = e.toString();
        isLoadingCompanies = false;
      });
    }
  }

  Future<void> handleLogin() async {
    final driverCode = driverIdController.text.trim().toUpperCase();
    final password = passwordController.text.trim();
    final companyKey = selectedCompanyKey.trim();

    if (companyKey.isEmpty || driverCode.isEmpty || password.isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('請選擇公司並輸入司機代碼與密碼')));
      return;
    }

    setState(() {
      isLoading = true;
    });

    try {
      final data = await ApiService.login(
        driverCode: driverCode,
        password: password,
        companyKey: companyKey,
      );

      final trackingWarning = await BackgroundLocationTracker.instance.start(
        (data['driver_code'] ?? driverCode).toString(),
      );

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(trackingWarning ?? '背景定位已啟動，正在回傳司機位置')),
      );

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
                              '請選擇所屬公司並輸入司機帳號',
                              style: TextStyle(
                                fontSize: 15,
                                color: Colors.grey,
                              ),
                            ),
                            const SizedBox(height: 28),
                            DropdownButtonFormField<String>(
                              initialValue:
                                  companies.any(
                                    (item) => item['key'] == selectedCompanyKey,
                                  )
                                  ? selectedCompanyKey
                                  : null,
                              decoration: InputDecoration(
                                labelText: '所屬公司',
                                border: const OutlineInputBorder(),
                                prefixIcon: const Icon(Icons.business_outlined),
                                suffixIcon: isLoadingCompanies
                                    ? const Padding(
                                        padding: EdgeInsets.all(14),
                                        child: SizedBox(
                                          width: 18,
                                          height: 18,
                                          child: CircularProgressIndicator(
                                            strokeWidth: 2,
                                          ),
                                        ),
                                      )
                                    : null,
                              ),
                              items: companies.map((company) {
                                return DropdownMenuItem<String>(
                                  value: company['key'],
                                  child: Text(company['name']!),
                                );
                              }).toList(),
                              onChanged: isLoadingCompanies
                                  ? null
                                  : (value) => setState(() {
                                      selectedCompanyKey = value ?? '';
                                    }),
                            ),
                            if (companyLoadError != null) ...[
                              const SizedBox(height: 8),
                              Row(
                                children: [
                                  const Expanded(
                                    child: Text(
                                      '無法讀取公司清單，請確認連線設定。',
                                      style: TextStyle(color: Colors.redAccent),
                                    ),
                                  ),
                                  TextButton(
                                    onPressed: loadCompanies,
                                    child: const Text('重試'),
                                  ),
                                ],
                              ),
                            ],
                            const SizedBox(height: 16),
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
                                onPressed:
                                    isLoading ||
                                        isLoadingCompanies ||
                                        companies.isEmpty
                                    ? null
                                    : handleLogin,
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
                                    onSaved: () async {
                                      if (mounted) {
                                        setState(() {});
                                        await loadCompanies();
                                      }
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
