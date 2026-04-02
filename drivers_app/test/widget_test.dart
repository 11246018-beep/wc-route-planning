import 'package:flutter_test/flutter_test.dart';
import 'package:driver_app/main.dart';

void main() {
  testWidgets('Driver app smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const DriverApp());

    expect(find.text('司機排程系統'), findsOneWidget);
    expect(find.text('登入'), findsOneWidget);
  });
}