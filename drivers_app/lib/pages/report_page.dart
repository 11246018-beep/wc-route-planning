part of '../main.dart';

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
